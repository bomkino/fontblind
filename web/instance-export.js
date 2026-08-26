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
  const OUTPUTS = Object.freeze({
    native: Object.freeze({
      filename: "fontblind-instance.ttf",
      mediaType: "font/ttf"
    }),
    web: Object.freeze({
      filename: "fontblind-instance.woff2",
      mediaType: "font/woff2"
    }),
    css: Object.freeze({
      filename: "fontblind-instance.css",
      mediaType: "text/css; charset=utf-8"
    }),
    bundle: Object.freeze({
      filename: "fontblind-instance-package.zip",
      mediaType: "application/zip"
    })
  });
  const SAFE_SERVER_ERRORS = new Set([
    "Invalid local host.",
    "Invalid local session.",
    "Invalid local upload.",
    "Invalid local Lab request.",
    "Invalid static export request.",
    "Invalid local upload framing.",
    "Local input is too large.",
    "Choose a TTF or OTF first.",
    "Choose one generated-axis location.",
    "Choose a valid Oblique output.",
    "Oblique angle must be between 4 and 20 degrees.",
    "The local upload was interrupted.",
    "Another local build is already running. Finish or reset it before starting another.",
    "Another local build is already running. Finish or reset it before freezing this position.",
    "This font is missing structure required by modern browsers. No output was kept.",
    "This font contains data FontBlind cannot yet prove zero-ID. No output was kept.",
    "This font could not satisfy the zero-ID fidelity checks. No output was kept.",
    "Processing failed safely. No output was kept.",
    "This two-axis set has no real base with independent weight and width extremes. Add the missing row or column masters. No output was kept.",
    "The donors do not expose unique, valid OpenType weight/width coordinates. No output was kept.",
    "The donors disagree on glyph order, character map, units, or interpolatable outline structure. No output was kept.",
    "The slant-axis lane needs an upright source at its 0-degree default. No output was kept.",
    "This Lab lane needs standalone static TrueType glyf fonts. No output was kept.",
    "The local compiler could not prove this Lab build safe and exact. No output was kept.",
    "This output has expired.",
    "This output expired or failed its retained-file integrity check.",
    "The generated variable source has expired.",
    "This output has no generated axis to freeze.",
    "Static export is unavailable in this local runtime.",
    "This generated position could not be frozen and verified. No output was kept.",
    "Static export failed safely. No output was kept.",
    "The frozen output expired before it could be exposed.",
    "Too many verified downloads are already active. Try this download again.",
    "The local service returned an incoherent proof or package. Outputs were discarded.",
    "Not found.",
    "Output unavailable."
  ]);

  class InstanceExportError extends Error {}
  class StaleInstanceOperation extends Error {}

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value);
  }

  function exactKeys(value, expected, context) {
    if (!object(value)) throw new InstanceExportError(`${context} must be an object.`);
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
      throw new InstanceExportError(`${context} returned unexpected fields.`);
    }
  }

  function axisNumber(value) {
    if (Number.isInteger(value)) return String(value);
    return String(Math.round(value * 1000) / 1000);
  }

  function formatLocation(axes, location) {
    return axes.map((axis) => `${axis.tag} ${axisNumber(location[axis.tag])}`).join(" · ");
  }

  function localPath(value, origin, expectedJob = null, expectedKind = null) {
    if (typeof value !== "string") {
      throw new InstanceExportError("Static export returned an incomplete download.");
    }
    let parsed;
    try {
      parsed = new URL(value, `${origin}/`);
    } catch (_) {
      throw new InstanceExportError("Static export returned an invalid download.");
    }
    const match = /^\/download\/([a-f0-9]{32})\/(native|web|css|bundle)$/.exec(parsed.pathname);
    if (parsed.origin !== origin || parsed.search || parsed.hash || !match ||
        (expectedJob !== null && match[1] !== expectedJob) ||
        (expectedKind !== null && match[2] !== expectedKind)) {
      throw new InstanceExportError("Static export returned a non-local or incoherent download.");
    }
    return parsed.pathname;
  }

  function validateLocation(raw, expected = null) {
    if (!object(raw)) {
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
      if (!object(expected) || Object.keys(expected).sort().join("+") !== signature) {
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

  function validateOutput(item, kind, job, origin) {
    const expected = OUTPUTS[kind];
    exactKeys(item, ["kind", "filename", "media_type", "url"], `${kind} output`);
    if (item.kind !== kind || item.filename !== expected.filename || item.media_type !== expected.mediaType) {
      throw new InstanceExportError(`Static export returned an incoherent ${kind} descriptor.`);
    }
    localPath(item.url, origin, job, kind);
  }

  function validateStaticResult(data, origin = "http://127.0.0.1", expectedLocation = null) {
    exactKeys(
      data,
      ["ok", "job", "location", "native", "web", "css", "bundle", "checks"],
      "Static export"
    );
    if (data.ok !== true || typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) {
      throw new InstanceExportError("Static export returned an incomplete result.");
    }
    for (const kind of ["native", "web", "css", "bundle"]) {
      validateOutput(data[kind], kind, data.job, origin);
    }
    if (!object(data.checks)) {
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

  function sameLocation(axes, left, right) {
    if (!Array.isArray(axes) || !object(left) || !object(right)) return false;
    return axes.every((axis) => {
      const leftValue = left[axis.tag];
      const rightValue = right[axis.tag];
      const minimum = Number(axis.min);
      const maximum = Number(axis.max);
      if (![leftValue, rightValue, minimum, maximum].every(
        (value) => typeof value === "number" && Number.isFinite(value)
      )) return false;
      const tolerance = Math.max(0.000001, Math.abs(maximum - minimum) / 1000000);
      return Math.abs(leftValue - rightValue) <= tolerance;
    });
  }

  function createOperationLedger() {
    const versions = new Map();

    function bump(scope) {
      const next = (versions.get(scope) || 0) + 1;
      versions.set(scope, next);
      return next;
    }

    function current(ticket) {
      return object(ticket) && typeof ticket.scope === "string" &&
        Number.isInteger(ticket.version) && versions.get(ticket.scope) === ticket.version;
    }

    return Object.freeze({
      begin(scope) {
        if (typeof scope !== "string" || !scope) throw new TypeError("Operation scope must be a non-empty string.");
        return Object.freeze({ scope, version: bump(scope) });
      },
      cancel(scope) {
        if (typeof scope === "string" && scope) bump(scope);
      },
      current,
      complete(ticket) {
        if (current(ticket)) bump(ticket.scope);
      }
    });
  }

  function safeErrorMessage(status, context = "build") {
    if (context === "session") {
      return "The local session could not start. Reload FontBlind and try again.";
    }
    if (status === 403) {
      return "The local session expired. Reload FontBlind and try again.";
    }
    if (status === 404) {
      return context === "instance"
        ? "The generated variable source expired before this position could be frozen."
        : "This local output has expired. Build it again.";
    }
    if (status === 413) {
      return "This local request exceeded FontBlind’s accepted size. No output was kept.";
    }
    if (status === 429) {
      return context === "instance"
        ? "Another local build is already running. Finish it before freezing this position."
        : "Another local build is already running. Finish or reset it before starting another.";
    }
    if (status === 400 || status === 415) {
      return context === "instance"
        ? "FontBlind rejected this static-position request. No output was kept."
        : "FontBlind rejected this local request. No output was kept.";
    }
    if (status === 422) {
      return context === "instance"
        ? "This generated position could not be frozen and verified. No output was kept."
        : "This font could not satisfy the selected workbench contract. No output was kept.";
    }
    return context === "instance"
      ? "Static export failed safely. No output was kept."
      : "Processing failed safely. No output was kept.";
  }

  function requestDetails(target, input, init) {
    const value = typeof input === "string" || input instanceof URL ? input : input && input.url;
    let path = "";
    try {
      path = new URL(value, target.location.href).pathname;
    } catch (_) { /* non-URL request */ }
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    return { path, method };
  }

  function errorContext(path) {
    if (path === "/api/session") return "session";
    if (/^\/api\/jobs\/[a-f0-9]{32}\/instance$/.test(path)) return "instance";
    return "build";
  }

  function authoredError(data) {
    if (!object(data)) return null;
    const keys = Object.keys(data).sort();
    if (keys.length !== 2 || keys[0] !== "error" || keys[1] !== "ok" ||
        data.ok !== false || typeof data.error !== "string" || !SAFE_SERVER_ERRORS.has(data.error)) {
      return null;
    }
    return data.error;
  }

  async function filteredError(target, response, path) {
    let message = null;
    try {
      message = authoredError(await response.clone().json());
    } catch (_) { /* malformed response */ }
    if (!message) message = safeErrorMessage(response.status, errorContext(path));
    return new target.Response(
      JSON.stringify({ ok: false, error: message }),
      {
        status: response.status >= 400 && response.status <= 599 ? response.status : 500,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store, max-age=0",
          "Pragma": "no-cache",
          "X-Content-Type-Options": "nosniff"
        }
      }
    );
  }

  function installErrorFirewall(target) {
    if (target.__fontBlindErrorFirewallInstalled) return;
    target.__fontBlindErrorFirewallInstalled = true;
    const upstream = target.fetch.bind(target);
    target.fetch = async function fontBlindErrorFirewall(input, init) {
      const details = requestDetails(target, input, init);
      const response = await upstream(input, init);
      const localSurface = details.path.startsWith("/api/") || details.path.startsWith("/download/");
      if (!localSurface || response.ok) return response;
      return filteredError(target, response, details.path);
    };
  }

  const exported = {
    InstanceExportError,
    createOperationLedger,
    formatLocation,
    installErrorFirewall,
    localPath,
    safeErrorMessage,
    sameLocation,
    validateStaticResult
  };
  root.FontBlindInstanceExport = Object.freeze(exported);
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (typeof document === "undefined" || typeof root.fetch !== "function") return;

  installErrorFirewall(root);
  const transportFetch = root.fetch.bind(root);
  const parents = new Map();
  const parentTools = new Map();
  const children = new Map();
  const childFaces = new Map();
  const frozenLocations = new Map();
  const pendingOperations = new Map();
  const operations = createOperationLedger();
  let sessionSecret = "";
  let faceSerial = 0;
  let mountScheduled = false;

  function requestHeaders(input, init) {
    try {
      return new root.Headers((init && init.headers) || (input && input.headers) || undefined);
    } catch (_) {
      return new root.Headers();
    }
  }

  function shellFor(parentToken) {
    return document.querySelector(`[data-instance-export][data-parent-token="${parentToken}"]`);
  }

  async function deleteToken(token, keepalive = false) {
    if (!token) return;
    try {
      await transportFetch(`/api/jobs/${token}`, {
        method: "DELETE",
        headers: { "X-FontBlind-Session": sessionSecret },
        keepalive,
        cache: "no-store",
        credentials: "same-origin"
      });
    } catch (_) { /* local shutdown */ }
  }

  function removeFace(face) {
    if (!face) return;
    try {
      document.fonts.delete(face);
    } catch (_) { /* detached font set */ }
  }

  function removeChildFace(parentToken) {
    const face = childFaces.get(parentToken);
    childFaces.delete(parentToken);
    removeFace(face);
  }

  function clearFrozenUi(parentToken, statusText = "") {
    const shell = shellFor(parentToken);
    if (!shell) return;
    delete shell.dataset.frozenLocation;
    const output = shell.querySelector("[data-instance-output]");
    if (output) {
      output.replaceChildren();
      output.removeAttribute("aria-busy");
    }
    const status = shell.querySelector("[data-instance-status]");
    if (status && statusText) status.textContent = statusText;
  }

  async function discardChild(parentToken, keepalive = false, statusText = "") {
    const childToken = children.get(parentToken);
    children.delete(parentToken);
    frozenLocations.delete(parentToken);
    removeChildFace(parentToken);
    clearFrozenUi(parentToken, statusText);
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

  function operationUsable(tool, parent, ticket, chosen, shell) {
    const currentParent = parents.get(tool);
    const current = currentLocation(tool, parent.axes);
    return operations.current(ticket) && shell.isConnected &&
      currentParent && currentParent.token === parent.token &&
      current && sameLocation(parent.axes, chosen, current);
  }

  function renderProof(container, checks) {
    const proof = document.createElement("div");
    proof.className = "proof";
    proof.setAttribute("aria-label", "Frozen instance verification checks");
    const heading = document.createElement("h3");
    heading.textContent = "PROOF AT THIS POSITION";
    const list = document.createElement("ul");
    for (const [key, passed] of Object.entries({ ...checks, browser_font_loaded: true })) {
      const label = CHECK_LABELS[key] || key.replace(/_/g, " ");
      const row = document.createElement("li");
      row.className = passed === true ? "is-pass" : "is-fail";
      row.setAttribute("aria-label", `${passed === true ? "Passed" : "Failed"}: ${label}`);
      const dot = document.createElement("i");
      dot.setAttribute("aria-hidden", "true");
      row.append(dot, document.createTextNode(label));
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
      link.href = localPath(data[kind].url, root.location.origin, data.job, kind);
      link.setAttribute("aria-label", `${copy[kind][1]}. ${copy[kind][2]}`);
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

  async function prepareFrozenFace(tool, data, specimen) {
    const family = `FontBlindFrozen-${tool}-${++faceSerial}`;
    const face = new root.FontFace(
      family,
      `url(${JSON.stringify(localPath(data.web.url, root.location.origin, data.job, "web"))})`,
      { display: "block" }
    );
    await face.load();
    document.fonts.add(face);
    if (!document.fonts.check(`32px ${JSON.stringify(family)}`, "Hamburgefontsiv")) {
      removeFace(face);
      throw new InstanceExportError("This browser rejected the frozen WOFF2.");
    }
    specimen.style.fontFamily = `${JSON.stringify(family)}, sans-serif`;
    specimen.classList.add("is-loaded");
    return face;
  }

  function buildFrozenOutput(machine, parent, data) {
    const frozenLocation = data.location;
    const frozenLabel = formatLocation(parent.axes, frozenLocation);
    const fragment = document.createDocumentFragment();

    const line = document.createElement("div");
    line.className = "success-line";
    const pass = document.createElement("span");
    pass.textContent = "PASS";
    line.append(pass, document.createTextNode(` Static instance frozen at ${frozenLabel}`));

    const specimen = document.createElement("textarea");
    specimen.className = "specimen";
    specimen.rows = 2;
    specimen.spellcheck = false;
    specimen.autocomplete = "off";
    specimen.setAttribute("autocapitalize", "off");
    specimen.value = machine.querySelector("[data-specimen]").value;
    specimen.setAttribute("aria-label", `Frozen static preview at ${frozenLabel}`);

    fragment.append(line, specimen);
    renderDownloads(fragment, data);
    renderProof(fragment, data.checks);
    return { fragment, specimen, frozenLabel };
  }

  function invalidateMovedState(parent, tool, current) {
    const pending = pendingOperations.get(parent.token);
    if (pending && !sameLocation(parent.axes, pending.location, current)) {
      operations.cancel(parent.token);
      const shell = shellFor(parent.token);
      const status = shell && shell.querySelector("[data-instance-status]");
      if (status) {
        status.textContent = "Coordinates changed during verification. That in-flight result will be discarded.";
      }
    }

    const frozen = frozenLocations.get(parent.token);
    if (frozen && !sameLocation(parent.axes, frozen, current)) {
      operations.cancel(parent.token);
      void discardChild(
        parent.token,
        false,
        "Live coordinates changed. The old static package was discarded; freeze this position again."
      );
    }
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
      const idBase = `fontblind-instance-${parent.token}`;
      shell = document.createElement("section");
      shell.className = "master-map-shell";
      shell.dataset.instanceExport = "true";
      shell.dataset.parentToken = parent.token;
      shell.setAttribute("aria-labelledby", `${idBase}-title`);

      const heading = document.createElement("div");
      heading.className = "master-map-heading";
      const title = document.createElement("strong");
      title.id = `${idBase}-title`;
      title.textContent = "FREEZE A STATIC INSTANCE";
      const note = document.createElement("span");
      note.textContent = "Uses only this verified generated variable font. Original donors are not reopened.";
      heading.append(title, note);

      const location = document.createElement("p");
      location.id = `${idBase}-location`;
      location.className = "result-truth";
      location.dataset.instanceLocation = "true";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "again";
      button.textContent = "Freeze current position";
      button.setAttribute("aria-describedby", `${idBase}-location ${idBase}-status`);

      const status = document.createElement("p");
      status.id = `${idBase}-status`;
      status.className = "result-truth";
      status.dataset.instanceStatus = "true";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      status.setAttribute("aria-atomic", "true");
      status.textContent = "Choose any point in the verified designspace, then freeze it.";

      const output = document.createElement("div");
      output.dataset.instanceOutput = "true";
      output.setAttribute("role", "region");
      output.setAttribute("aria-label", "Frozen static instance result");
      output.setAttribute("aria-live", "polite");
      output.tabIndex = -1;

      button.addEventListener("click", async () => {
        const chosen = currentLocation(tool, parent.axes);
        if (!chosen) {
          status.textContent = "The current generated coordinates are invalid.";
          return;
        }

        const ticket = operations.begin(parent.token);
        pendingOperations.set(parent.token, { ticket, location: { ...chosen } });
        button.disabled = true;
        shell.setAttribute("aria-busy", "true");
        output.setAttribute("aria-busy", "true");
        status.textContent = `Freezing ${formatLocation(parent.axes, chosen)} and rerunning every exit gate…`;

        let createdToken = null;
        let preparedFace = null;
        let committed = false;
        try {
          const response = await transportFetch(`/api/jobs/${parent.token}/instance`, {
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
          if (data && typeof data.job === "string" && /^[a-f0-9]{32}$/.test(data.job)) {
            createdToken = data.job;
          }
          if (!response.ok || !data || data.ok !== true) {
            throw new InstanceExportError(safeErrorMessage(response.status, "instance"));
          }

          validateStaticResult(data, root.location.origin, chosen);
          if (!operationUsable(tool, parent, ticket, chosen, shell)) {
            throw new StaleInstanceOperation();
          }

          const next = buildFrozenOutput(machine, parent, data);
          preparedFace = await prepareFrozenFace(tool, data, next.specimen);
          if (!operationUsable(tool, parent, ticket, chosen, shell)) {
            throw new StaleInstanceOperation();
          }

          const previousToken = children.get(parent.token);
          const previousFace = childFaces.get(parent.token);
          children.set(parent.token, data.job);
          childFaces.set(parent.token, preparedFace);
          frozenLocations.set(parent.token, { ...data.location });
          output.replaceChildren(...Array.from(next.fragment.childNodes));
          shell.dataset.frozenLocation = next.frozenLabel;
          removeFace(previousFace);
          if (previousToken && previousToken !== data.job) void deleteToken(previousToken);

          preparedFace = null;
          committed = true;
          status.textContent = `Static package ready at ${next.frozenLabel}. Moving a live axis discards it rather than relabelling it.`;
          output.focus({ preventScroll: false });
        } catch (error) {
          removeFace(preparedFace);
          if (createdToken && children.get(parent.token) !== createdToken) {
            await deleteToken(createdToken);
          }
          if (createdToken && !(error instanceof StaleInstanceOperation)) {
            children.delete(parent.token);
            frozenLocations.delete(parent.token);
            removeChildFace(parent.token);
            clearFrozenUi(parent.token);
          }
          if (!(error instanceof StaleInstanceOperation)) {
            status.textContent = error instanceof InstanceExportError && error.message
              ? error.message
              : "Static export failed safely. No output was kept.";
          }
        } finally {
          const pending = pendingOperations.get(parent.token);
          if (pending && pending.ticket === ticket) pendingOperations.delete(parent.token);
          if (operations.current(ticket)) operations.complete(ticket);
          if (shell.isConnected && parents.get(tool)?.token === parent.token) {
            button.disabled = false;
            shell.removeAttribute("aria-busy");
            output.removeAttribute("aria-busy");
          }
          if (!committed) scheduleMount();
        }
      });

      shell.append(heading, location, button, status, output);
      panel.append(shell);
    }

    const now = currentLocation(tool, parent.axes);
    const locationNode = shell.querySelector("[data-instance-location]");
    if (now && locationNode) {
      invalidateMovedState(parent, tool, now);
      const frozen = frozenLocations.get(parent.token);
      const currentLabel = formatLocation(parent.axes, now);
      const frozenLabel = frozen ? formatLocation(parent.axes, frozen) : "";
      const nextText = frozenLabel
        ? `CURRENT ${currentLabel} · FROZEN PACKAGE ${frozenLabel}`
        : `CURRENT ${currentLabel}`;
      if (locationNode.textContent !== nextText) locationNode.textContent = nextText;
    }
  }

  function installAccessibility() {
    const document = root.document;
    let skip = document.querySelector("[data-skip-workspace]");
    if (!skip) {
      skip = document.createElement("a");
      skip.dataset.skipWorkspace = "true";
      skip.className = "again";
      skip.textContent = "Skip to active workspace";
      skip.style.position = "fixed";
      skip.style.zIndex = "100";
      skip.style.top = "0.75rem";
      skip.style.left = "0.75rem";
      skip.style.transform = "translateY(-180%)";
      skip.style.background = "var(--paper)";
      skip.style.color = "var(--ink)";
      skip.addEventListener("focus", () => { skip.style.transform = "translateY(0)"; });
      skip.addEventListener("blur", () => { skip.style.transform = "translateY(-180%)"; });
      document.body.prepend(skip);
    }

    const visibility = new WeakMap();

    function syncWorkspaces() {
      const active = document.body.dataset.activeTool || "blind";
      const nav = document.querySelector(".workbench-index");
      if (nav) nav.setAttribute("role", "tablist");
      document.querySelectorAll(".tool-link[data-workspace-target]").forEach((tab) => {
        const name = tab.dataset.workspaceTarget;
        const selected = name === active;
        tab.id = `fontblind-tab-${name}`;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", `fontblind-workspace-${name}`);
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
      });
      document.querySelectorAll("[data-workspace]").forEach((workspace) => {
        const name = workspace.dataset.workspace;
        workspace.id = `fontblind-workspace-${name}`;
        workspace.setAttribute("role", "tabpanel");
        workspace.setAttribute("aria-labelledby", `fontblind-tab-${name}`);
        workspace.tabIndex = -1;
      });
      skip.href = `#fontblind-workspace-${active}`;
    }

    function syncBuildControls(machine, busy) {
      machine.querySelectorAll(".angle-bench input, .angle-bench select, .angle-bench button").forEach((control) => {
        if (busy) {
          if (control.dataset.fontblindWasDisabled === undefined) {
            control.dataset.fontblindWasDisabled = control.disabled ? "true" : "false";
          }
          control.disabled = true;
        } else if (control.dataset.fontblindWasDisabled !== undefined) {
          control.disabled = control.dataset.fontblindWasDisabled === "true";
          delete control.dataset.fontblindWasDisabled;
        }
      });
      const bench = machine.querySelector(".angle-bench");
      if (bench) {
        bench.toggleAttribute("inert", busy);
        bench.setAttribute("aria-disabled", busy ? "true" : "false");
      }
    }

    function enhanceProofRows(scope) {
      scope.querySelectorAll(".proof li").forEach((row) => {
        if (row.hasAttribute("aria-label")) return;
        const label = row.textContent.trim();
        row.setAttribute(
          "aria-label",
          `${row.classList.contains("is-fail") ? "Failed" : "Passed"}: ${label}`
        );
      });
    }

    function syncMachine(machine) {
      const processing = machine.querySelector("[data-processing]");
      const result = machine.querySelector("[data-result]");
      const error = machine.querySelector("[data-error]");
      const busy = Boolean(processing && !processing.hidden);
      machine.setAttribute("aria-busy", busy ? "true" : "false");
      syncBuildControls(machine, busy);

      if (processing) {
        processing.setAttribute("role", "status");
        processing.setAttribute("aria-atomic", "true");
        processing.tabIndex = -1;
      }
      if (result) {
        result.setAttribute("role", "region");
        result.setAttribute("aria-label", `${machine.dataset.machine} verified result`);
        result.setAttribute("aria-live", "polite");
        result.tabIndex = -1;
      }
      if (error) error.tabIndex = -1;

      const dropzone = machine.querySelector("[data-dropzone]");
      const dropSub = dropzone && dropzone.querySelector(".drop-sub");
      if (dropzone && dropSub) {
        if (!dropSub.id) dropSub.id = `fontblind-${machine.dataset.machine}-drop-help`;
        dropzone.setAttribute("aria-describedby", dropSub.id);
      }

      for (const view of [processing, result, error]) {
        if (!view) continue;
        const wasHidden = visibility.get(view);
        const isHidden = view.hidden;
        visibility.set(view, isHidden);
        if (wasHidden === true && isHidden === false) {
          queueMicrotask(() => {
            if (view.hidden || !view.isConnected) return;
            if (!view.contains(document.activeElement)) {
              view.focus({ preventScroll: false });
            }
          });
        }
      }
      enhanceProofRows(machine);
    }

    skip.addEventListener("click", () => {
      queueMicrotask(() => {
        const active = document.querySelector(`[data-workspace="${document.body.dataset.activeTool || "blind"}"]`);
        if (active) active.focus({ preventScroll: false });
      });
    });

    syncWorkspaces();
    document.querySelectorAll("[data-machine]").forEach(syncMachine);

    const observer = new MutationObserver((records) => {
      let workspaceChange = false;
      const machines = new Set();
      for (const record of records) {
        if (record.target === document.body || record.attributeName === "hidden") workspaceChange = true;
        const machine = record.target instanceof Element
          ? record.target.closest("[data-machine]")
          : null;
        if (machine) machines.add(machine);
        for (const node of record.addedNodes || []) {
          if (!(node instanceof Element)) continue;
          if (node.matches("[data-machine]")) machines.add(node);
          node.querySelectorAll?.("[data-machine]").forEach((item) => machines.add(item));
        }
      }
      if (workspaceChange) syncWorkspaces();
      machines.forEach(syncMachine);
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["hidden", "class", "data-active-tool"]
    });
  }

  const upstreamFetch = root.fetch.bind(root);
  root.fetch = async function fontBlindFetch(input, init) {
    const details = requestDetails(root, input, init);
    const headers = requestHeaders(input, init);
    const suppliedSession = headers.get("X-FontBlind-Session");
    if (suppliedSession) sessionSecret = suppliedSession;

    const parentDelete = details.method === "DELETE" &&
      /^\/api\/jobs\/[a-f0-9]{32}$/.test(details.path);
    if (parentDelete) {
      const token = details.path.split("/").pop();
      if (parentTools.has(token)) {
        operations.cancel(token);
        pendingOperations.delete(token);
        void discardChild(token, Boolean(init && init.keepalive));
        const tool = parentTools.get(token);
        parentTools.delete(token);
        parents.delete(tool);
      }
    }

    const response = await upstreamFetch(input, init);
    const labTool = details.method === "POST" && details.path === "/api/lab/variable"
      ? "variable"
      : details.method === "POST" && details.path === "/api/lab/oblique"
        ? "oblique"
        : null;
    if (labTool && response.ok) {
      response.clone().json().then(async (data) => {
        if (!data || data.ok !== true || !Array.isArray(data.axes) || !data.axes.length ||
            typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) return;
        const previous = parents.get(labTool);
        if (previous && previous.token !== data.job) {
          operations.cancel(previous.token);
          pendingOperations.delete(previous.token);
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

  installAccessibility();

  const observer = new MutationObserver(scheduleMount);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["hidden", "class", "aria-pressed"]
  });
  document.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement &&
        /^(oblique|variable)-axis-/.test(event.target.id)) {
      scheduleMount();
    }
  }, true);
  root.addEventListener("pagehide", () => {
    for (const parentToken of pendingOperations.keys()) operations.cancel(parentToken);
    for (const parentToken of children.keys()) {
      operations.cancel(parentToken);
      void discardChild(parentToken, true);
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
