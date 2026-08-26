"use strict";

(function initialiseInstanceExport(root) {
  const STATIC_CHECKS = new Set([
    "source_identity_removed",
    "embedding_flags_cleared",
    "selected_location_verified",
    "static_instance_verified",
    "variation_tables_removed",
    "axis_metadata_verified",
    "harfbuzz_shaping_verified",
    "woff2_roundtrip_verified",
    "source_discarded"
  ]);
  const CHECK_LABELS = {
    source_identity_removed: "Original identity labels remain absent",
    embedding_flags_cleared: "Embedding labels remain cleared",
    selected_location_verified: "Selected coordinates reproduced exactly",
    static_instance_verified: "Static outlines and metrics verified",
    variation_tables_removed: "Variable-font machinery removed",
    axis_metadata_verified: "Static weight, width, and slant metadata verified",
    harfbuzz_shaping_verified: "HarfBuzz shaping matched the selected position",
    woff2_roundtrip_verified: "WOFF2 decoded and checked",
    source_discarded: "Intermediate generated source discarded",
    browser_font_loaded: "Browser loaded the frozen WOFF2"
  };

  class InstanceExportError extends Error {}

  function axisNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
  }

  function formatLocation(axes, location) {
    return axes.map((axis) => `${axis.tag} ${axisNumber(location[axis.tag])}`).join(" · ");
  }

  function localPath(value, origin) {
    if (typeof value !== "string") throw new InstanceExportError("Static export returned an incomplete download.");
    const parsed = new URL(value, `${origin}/`);
    if (parsed.origin !== origin || !/^\/download\/[a-f0-9]{32}\/(native|web|css|bundle)$/.test(parsed.pathname)) {
      throw new InstanceExportError("Static export returned a non-local download.");
    }
    return `${parsed.pathname}${parsed.search}`;
  }

  function validateLocation(raw, expected = null) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new InstanceExportError("Static export returned no verified location.");
    }
    const tags = Object.keys(raw).sort();
    const signature = tags.join("+");
    if (!["slnt", "wdth", "wght", "wdth+wght"].includes(signature)) {
      throw new InstanceExportError("Static export returned an invalid generated-axis location.");
    }
    const location = {};
    for (const tag of tags) {
      const value = raw[tag];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new InstanceExportError("Static export returned an invalid generated-axis coordinate.");
      }
      location[tag] = value;
    }
    if (expected !== null) {
      if (!expected || typeof expected !== "object" || Array.isArray(expected) ||
          Object.keys(expected).sort().join("+") !== signature) {
        throw new InstanceExportError("Static export confirmed a different generated-axis model.");
      }
      for (const tag of tags) {
        if (typeof expected[tag] !== "number" || !Number.isFinite(expected[tag]) ||
            Math.abs(expected[tag] - location[tag]) > 0.000001) {
          throw new InstanceExportError("Static export confirmed a different generated-axis location.");
        }
      }
    }
    return location;
  }

  function validateStaticResult(data, origin = "http://127.0.0.1", expectedLocation = null) {
    if (!data || data.ok !== true || typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) {
      throw new InstanceExportError("Static export returned an incomplete result.");
    }
    if (data.axes !== undefined || data.masters !== undefined) {
      throw new InstanceExportError("Static export unexpectedly remained variable.");
    }
    for (const kind of ["native", "web", "css", "bundle"]) {
      if (!data[kind] || typeof data[kind] !== "object") {
        throw new InstanceExportError("Static export returned an incomplete package.");
      }
      localPath(data[kind].url, origin);
    }
    if (!data.checks || typeof data.checks !== "object" || Array.isArray(data.checks)) {
      throw new InstanceExportError("Static export returned no verification proof.");
    }
    const checks = Object.entries(data.checks);
    if (checks.length !== STATIC_CHECKS.size ||
        checks.some(([key, passed]) => !STATIC_CHECKS.has(key) || passed !== true) ||
        Array.from(STATIC_CHECKS).some((key) => data.checks[key] !== true)) {
      throw new InstanceExportError("Static export returned the wrong verification contract.");
    }
    data.location = validateLocation(data.location, expectedLocation);
    return data;
  }

  const exported = { InstanceExportError, formatLocation, validateStaticResult };
  root.FontBlindInstanceExport = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (typeof document === "undefined" || typeof root.fetch !== "function") return;

  const originalFetch = root.fetch.bind(root);
  const parents = new Map();
  const parentTools = new Map();
  const children = new Map();
  const childFaces = new Map();
  let sessionSecret = "";
  let faceSerial = 0;
  let mountScheduled = false;

  function requestPath(input) {
    const value = typeof input === "string" || input instanceof URL ? input : input && input.url;
    try {
      return new URL(value, root.location.href).pathname;
    } catch (_) {
      return "";
    }
  }

  function requestMethod(input, init) {
    return String((init && init.method) || (input && input.method) || "GET").toUpperCase();
  }

  function requestHeaders(input, init) {
    try {
      return new Headers((init && init.headers) || (input && input.headers) || undefined);
    } catch (_) {
      return new Headers();
    }
  }

  async function deleteToken(token, keepalive = false) {
    if (!token) return;
    try {
      await originalFetch(`/api/jobs/${token}`, {
        method: "DELETE",
        headers: { "X-FontBlind-Session": sessionSecret },
        keepalive,
        cache: "no-store",
        credentials: "same-origin"
      });
    } catch (_) { /* local shutdown */ }
  }

  function removeChildFace(parentToken) {
    const face = childFaces.get(parentToken);
    if (face) {
      document.fonts.delete(face);
      childFaces.delete(parentToken);
    }
  }

  async function discardChild(parentToken, keepalive = false) {
    const childToken = children.get(parentToken);
    children.delete(parentToken);
    removeChildFace(parentToken);
    await deleteToken(childToken, keepalive);
  }

  function scheduleMount() {
    if (mountScheduled) return;
    mountScheduled = true;
    queueMicrotask(() => {
      mountScheduled = false;
      for (const tool of parents.keys()) mount(tool);
    });
  }

  root.fetch = async function fontBlindFetch(input, init) {
    const path = requestPath(input);
    const method = requestMethod(input, init);
    const headers = requestHeaders(input, init);
    const suppliedSession = headers.get("X-FontBlind-Session");
    if (suppliedSession) sessionSecret = suppliedSession;

    const parentDelete = method === "DELETE" && /^\/api\/jobs\/[a-f0-9]{32}$/.test(path);
    if (parentDelete) {
      const token = path.split("/").pop();
      if (parentTools.has(token)) {
        void discardChild(token, Boolean(init && init.keepalive));
        const tool = parentTools.get(token);
        parentTools.delete(token);
        parents.delete(tool);
      }
    }

    const response = await originalFetch(input, init);
    const labTool = method === "POST" && path === "/api/lab/variable"
      ? "variable"
      : method === "POST" && path === "/api/lab/oblique"
        ? "oblique"
        : null;
    if (labTool) {
      response.clone().json().then(async (data) => {
        if (!data || data.ok !== true || !Array.isArray(data.axes) || !data.axes.length ||
            typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) return;
        const previous = parents.get(labTool);
        if (previous && previous.token !== data.job) {
          parentTools.delete(previous.token);
          await discardChild(previous.token);
        }
        const record = { token: data.job, axes: data.axes.map((axis) => ({ ...axis })) };
        parents.set(labTool, record);
        parentTools.set(data.job, labTool);
        scheduleMount();
      }).catch(() => {});
    }
    return response;
  };

  function currentLocation(tool, axes) {
    const location = {};
    for (const axis of axes) {
      const control = document.getElementById(`${tool}-axis-${axis.tag}`);
      const value = Number(control && control.value);
      if (!Number.isFinite(value) || value < axis.min || value > axis.max) return null;
      location[axis.tag] = value;
    }
    return location;
  }

  function renderProof(container, checks) {
    const proof = document.createElement("div");
    proof.className = "proof";
    proof.setAttribute("aria-label", "Frozen instance verification checks");
    const heading = document.createElement("h3");
    heading.textContent = "PROOF AT THIS POSITION";
    const list = document.createElement("ul");
    for (const [key, passed] of Object.entries({ ...checks, browser_font_loaded: true })) {
      const row = document.createElement("li");
      row.className = passed === true ? "is-pass" : "is-fail";
      const dot = document.createElement("i");
      dot.setAttribute("aria-hidden", "true");
      row.append(dot, document.createTextNode(CHECK_LABELS[key] || key.replace(/_/g, " ")));
      list.append(row);
    }
    proof.append(heading, list);
    container.append(proof);
  }

  function renderDownloads(container, data) {
    const downloads = document.createElement("div");
    downloads.className = "downloads";
    const copy = {
      native: ["DESKTOP", "Static TTF", "Selected outlines, no variation tables"],
      web: ["WEB", "Static WOFF2", "Browser-ready frozen position"],
      css: ["CSS", "Static @font-face", "Selected weight, width, and slant"],
      bundle: ["COMPLETE PACKAGE", "Take the frozen set", "TTF + WOFF2 + clean CSS"]
    };
    for (const kind of ["native", "web", "css", "bundle"]) {
      const link = document.createElement("a");
      link.className = `download-card${kind === "bundle" ? " bundle-card" : ""}`;
      link.href = localPath(data[kind].url, root.location.origin);
      const label = document.createElement("span");
      label.className = "file-kind";
      label.textContent = copy[kind][0];
      const title = document.createElement("strong");
      title.textContent = copy[kind][1];
      const note = document.createElement("small");
      note.textContent = copy[kind][2];
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "↓";
      link.append(label, title, note, arrow);
      downloads.append(link);
    }
    container.append(downloads);
  }

  async function loadFrozenPreview(tool, parentToken, data, specimen) {
    removeChildFace(parentToken);
    const family = `FontBlindFrozen-${tool}-${++faceSerial}`;
    const face = new FontFace(family, `url(${JSON.stringify(localPath(data.web.url, root.location.origin))})`, { display: "block" });
    await face.load();
    document.fonts.add(face);
    if (!document.fonts.check(`32px ${JSON.stringify(family)}`, "Hamburgefontsiv")) {
      document.fonts.delete(face);
      throw new InstanceExportError("This browser rejected the frozen WOFF2.");
    }
    childFaces.set(parentToken, face);
    specimen.style.fontFamily = `${JSON.stringify(family)}, sans-serif`;
    specimen.classList.add("is-loaded");
  }

  function mount(tool) {
    const parent = parents.get(tool);
    if (!parent) return;
    const machine = document.querySelector(`[data-machine="${tool}"]`);
    const panel = machine && machine.querySelector("[data-axis-panel]");
    const result = machine && machine.querySelector("[data-result]");
    if (!panel || panel.hidden || !result || result.hidden) return;

    let shell = panel.querySelector("[data-instance-export]");
    if (shell && shell.dataset.parentToken !== parent.token) {
      shell.remove();
      shell = null;
    }
    if (!shell) {
      shell = document.createElement("section");
      shell.className = "master-map-shell";
      shell.dataset.instanceExport = "true";
      shell.dataset.parentToken = parent.token;
      shell.setAttribute("aria-label", "Freeze the current generated position as a static font");

      const heading = document.createElement("div");
      heading.className = "master-map-heading";
      const title = document.createElement("strong");
      title.textContent = "FREEZE A STATIC INSTANCE";
      const note = document.createElement("span");
      note.textContent = "Uses only this verified generated variable font. Original donors are not reopened.";
      heading.append(title, note);

      const location = document.createElement("p");
      location.className = "result-truth";
      location.dataset.instanceLocation = "true";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "again";
      button.textContent = "Freeze current position";

      const status = document.createElement("p");
      status.className = "result-truth";
      status.dataset.instanceStatus = "true";
      status.setAttribute("aria-live", "polite");
      status.textContent = "Choose any point in the verified designspace, then freeze it.";

      const output = document.createElement("div");
      output.dataset.instanceOutput = "true";

      button.addEventListener("click", async () => {
        const chosen = currentLocation(tool, parent.axes);
        if (!chosen) {
          status.textContent = "The current generated coordinates are invalid.";
          return;
        }
        button.disabled = true;
        output.replaceChildren();
        status.textContent = `Freezing ${formatLocation(parent.axes, chosen)} and rerunning every exit gate…`;
        try {
          await discardChild(parent.token);
          const response = await originalFetch(`/api/jobs/${parent.token}/instance`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-FontBlind-Session": sessionSecret
            },
            body: JSON.stringify({ location: chosen }),
            cache: "no-store",
            credentials: "same-origin"
          });
          let data = null;
          try {
            data = await response.json();
          } catch (_) {
            throw new InstanceExportError("Static export stopped before returning a verified result.");
          }
          if (!response.ok || !data || data.ok !== true) {
            throw new InstanceExportError(data && typeof data.error === "string" ? data.error : "Static export failed safely.");
          }
          validateStaticResult(data, root.location.origin, chosen);
          const frozenLocation = data.location;
          children.set(parent.token, data.job);
          const specimen = document.createElement("textarea");
          specimen.className = "specimen";
          specimen.rows = 2;
          specimen.spellcheck = false;
          specimen.value = machine.querySelector("[data-specimen]").value;
          specimen.setAttribute("aria-label", `Frozen static preview at ${formatLocation(parent.axes, frozenLocation)}`);
          await loadFrozenPreview(tool, parent.token, data, specimen);

          const frozenLabel = formatLocation(parent.axes, frozenLocation);
          const line = document.createElement("div");
          line.className = "success-line";
          const pass = document.createElement("span");
          pass.textContent = "PASS";
          line.append(pass, document.createTextNode(` Static instance frozen at ${frozenLabel}`));
          output.append(line, specimen);
          renderDownloads(output, data);
          renderProof(output, data.checks);
          shell.dataset.frozenLocation = frozenLabel;
          status.textContent = "Static package ready. Moving the live sliders will not change this frozen package.";
        } catch (error) {
          await discardChild(parent.token);
          status.textContent = error instanceof Error && error.message
            ? error.message
            : "Static export failed safely. No output was kept.";
        } finally {
          button.disabled = false;
        }
      });

      shell.append(heading, location, button, status, output);
      panel.append(shell);
    }

    const now = currentLocation(tool, parent.axes);
    const locationNode = shell.querySelector("[data-instance-location]");
    if (now && locationNode) {
      const current = formatLocation(parent.axes, now);
      const frozen = shell.dataset.frozenLocation;
      const nextText = frozen && frozen !== current
        ? `CURRENT ${current} · FROZEN PACKAGE ${frozen}`
        : `CURRENT ${current}`;
      if (locationNode.textContent !== nextText) locationNode.textContent = nextText;
    }
  }

  const observer = new MutationObserver(scheduleMount);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["hidden", "class", "aria-pressed"]
  });
  document.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement && /^(oblique|variable)-axis-/.test(event.target.id)) scheduleMount();
  }, true);
  root.addEventListener("pagehide", () => {
    for (const parentToken of children.keys()) void discardChild(parentToken, true);
  });
})(typeof window !== "undefined" ? window : globalThis);
