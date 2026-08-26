"use strict";

const MAX_FONT_BYTES = 128 * 1024 * 1024;
const MAX_VARIABLE_BYTES = 256 * 1024 * 1024;
const TOOL_ORDER = ["blind", "oblique", "variable"];
const TOOL_TITLES = {
  blind: "FontBlind",
  oblique: "Oblique Lab — FontBlind",
  variable: "Variable Lab — FontBlind"
};
const DEFAULT_ERRORS = {
  blind: "This font could not satisfy the zero-ID fidelity checks. No output was kept.",
  oblique: "This font could not be transformed and verified as a mechanical Oblique. No output was kept.",
  variable: "These donors could not form one compatible weight/width system. No font was kept."
};
const CHECK_LABELS = {
  source_identity_removed: "Original identity labels gone",
  embedding_flags_cleared: "Embedding labels cleared",
  outline_flavor_retained: "Native outline flavour retained",
  functional_clone_verified: "Functional output verified",
  harfbuzz_shaping_verified: "HarfBuzz shaping passed",
  woff2_roundtrip_verified: "WOFF2 decoded and checked",
  browser_font_loaded: "Browser loaded the WOFF2",
  source_discarded: "Source discarded from the app",
  declared_shear_verified: "Declared mechanical slant verified",
  oblique_not_italic_verified: "Truthfully labelled Oblique, never Italic",
  hinting_removed: "Invalidated TrueType hints removed",
  donor_compatibility_verified: "Donor structure proved compatible",
  donor_instances_verified: "Every donor location matches exactly",
  independent_axis_model_verified: "Axes have real independent extremes",
  axis_metadata_verified: "Registered axis metadata verified",
  weight_axis_verified: "Weight axis verified",
  width_axis_verified: "Width axis verified",
  slant_axis_verified: "Slant axis verified",
  variable_endpoints_verified: "Upright and Oblique endpoints match"
};

class SafeMessage extends Error {}

const tools = new Map();
document.querySelectorAll("[data-machine]").forEach((machine) => {
  const name = machine.dataset.machine;
  tools.set(name, {
    name,
    machine,
    input: machine.querySelector("[data-file-input]"),
    dropzone: machine.querySelector("[data-dropzone]"),
    processing: machine.querySelector("[data-processing]"),
    result: machine.querySelector("[data-result]"),
    error: machine.querySelector("[data-error]"),
    errorText: machine.querySelector("[data-error-text]"),
    stepCount: machine.querySelector("[data-step-count]"),
    specimen: machine.querySelector("[data-specimen]"),
    checkList: machine.querySelector("[data-check-list]"),
    axisPanel: machine.querySelector("[data-axis-panel]")
  });
});

let sessionSecret = null;
let sessionRequest = null;
const activeJobs = new Map();
const previewFaces = new Map();
const axisValues = new Map();

function setView(name, state) {
  const ui = tools.get(name);
  ui.dropzone.hidden = state !== "drop";
  ui.processing.hidden = state !== "processing";
  ui.result.hidden = state !== "result";
  ui.error.hidden = state !== "error";
  ui.machine.classList.toggle("is-processing", state === "processing");
  ui.stepCount.textContent = {
    drop: "01 / 03",
    processing: "02 / 03",
    result: "03 / 03",
    error: "STOPPED"
  }[state];
}

function fail(name, message = DEFAULT_ERRORS[name]) {
  const ui = tools.get(name);
  ui.errorText.textContent = message;
  setView(name, "error");
}

function looksLikeOpenType(buffer) {
  if (buffer.byteLength < 4) return false;
  const head = new Uint8Array(buffer, 0, 4);
  const ascii = String.fromCharCode(...head);
  return (head[0] === 0 && head[1] === 1 && head[2] === 0 && head[3] === 0) ||
    ascii === "OTTO" || ascii === "true" || ascii === "typ1";
}

function looksLikeTrueType(buffer) {
  if (buffer.byteLength < 4) return false;
  const head = new Uint8Array(buffer, 0, 4);
  const ascii = String.fromCharCode(...head);
  return (head[0] === 0 && head[1] === 1 && head[2] === 0 && head[3] === 0) || ascii === "true";
}

function wipe(buffer) {
  if (buffer instanceof ArrayBuffer && buffer.byteLength) {
    new Uint8Array(buffer).fill(0);
  }
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 3 * 8192;
  let encoded = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
    encoded += btoa(String.fromCharCode(...chunk));
  }
  return encoded;
}

async function ensureSession() {
  if (sessionSecret) return sessionSecret;
  if (!sessionRequest) {
    sessionRequest = (async () => {
      const response = await fetch("/api/session", {
        cache: "no-store",
        credentials: "same-origin"
      });
      const data = await response.json();
      if (!response.ok || !data.ok || typeof data.session !== "string" || !data.session) {
        throw new SafeMessage("The local session could not start. No source was sent.");
      }
      sessionSecret = data.session;
      return sessionSecret;
    })();
  }
  try {
    return await sessionRequest;
  } finally {
    sessionRequest = null;
  }
}

function localPath(value) {
  if (typeof value !== "string") throw new SafeMessage("The local service returned an incomplete package. No download was exposed.");
  const parsed = new URL(value, window.location.href);
  if (parsed.origin !== window.location.origin) {
    throw new SafeMessage("The local service returned a non-local download. FontBlind refused it.");
  }
  return `${parsed.pathname}${parsed.search}`;
}

function assertPublicResult(data) {
  if (!data || data.ok !== true || typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) {
    throw new SafeMessage("The local service returned an incomplete result. No download was exposed.");
  }
  for (const kind of ["native", "web", "css", "bundle"]) {
    if (!data[kind] || typeof data[kind] !== "object") {
      throw new SafeMessage("The local service returned an incomplete package. No download was exposed.");
    }
    localPath(data[kind].url);
  }
  if (!data.checks || typeof data.checks !== "object" || Array.isArray(data.checks)) {
    throw new SafeMessage("The local service returned no verification proof. No download was exposed.");
  }
  if (data.axes !== undefined) {
    if (!Array.isArray(data.axes) || data.axes.length < 1 || data.axes.length > 3) {
      throw new SafeMessage("The local service returned invalid variation controls. No download was exposed.");
    }
    const allowed = new Set(["wght", "wdth", "slnt"]);
    for (const axis of data.axes) {
      const values = [axis && axis.min, axis && axis.default, axis && axis.max];
      if (!axis || !allowed.has(axis.tag) || typeof axis.name !== "string" ||
          values.some((value) => typeof value !== "number" || !Number.isFinite(value)) ||
          axis.min > axis.default || axis.default > axis.max) {
        throw new SafeMessage("The local service returned invalid variation controls. No download was exposed.");
      }
    }
  }
}

async function postLocal(endpoint, headers, body) {
  const session = await ensureSession();
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { ...headers, "X-FontBlind-Session": session },
    body,
    cache: "no-store",
    credentials: "same-origin"
  });
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    throw new SafeMessage("The local service stopped before returning a verified result. No output was kept.");
  }
  if (!response.ok || !data.ok) {
    throw new SafeMessage(typeof data.error === "string" ? data.error : "");
  }
  return data;
}

async function removePreview(name) {
  const face = previewFaces.get(name);
  if (face) {
    document.fonts.delete(face);
    previewFaces.delete(name);
  }
  const specimen = tools.get(name).specimen;
  specimen.classList.remove("is-loaded");
  specimen.style.removeProperty("font-family");
  specimen.style.removeProperty("font-variation-settings");
  axisValues.delete(name);
}

async function purge(name) {
  await removePreview(name);
  const token = activeJobs.get(name);
  if (token) {
    activeJobs.delete(name);
    try {
      await fetch(`/api/jobs/${token}`, {
        method: "DELETE",
        headers: { "X-FontBlind-Session": sessionSecret || "" },
        cache: "no-store",
        credentials: "same-origin"
      });
    } catch (_) { /* local shutdown */ }
  }
  const ui = tools.get(name);
  ui.checkList.replaceChildren();
  if (ui.axisPanel) {
    ui.axisPanel.replaceChildren();
    ui.axisPanel.hidden = true;
  }
  ui.machine.querySelectorAll("[data-download]").forEach((link) => link.setAttribute("href", "#"));
}

async function loadPreview(name, url) {
  await removePreview(name);
  const family = `FontBlindPreview-${name}`;
  const face = new FontFace(family, `url(${JSON.stringify(localPath(url))})`, { display: "block" });
  await face.load();
  document.fonts.add(face);
  if (!document.fonts.check(`32px ${JSON.stringify(family)}`, "Hamburgefontsiv")) {
    document.fonts.delete(face);
    throw new SafeMessage("This browser rejected the generated WOFF2. Outputs were discarded.");
  }
  previewFaces.set(name, face);
  const specimen = tools.get(name).specimen;
  specimen.style.fontFamily = `${JSON.stringify(family)}, sans-serif`;
  specimen.classList.add("is-loaded");
}

function readableCheck(key) {
  if (CHECK_LABELS[key]) return CHECK_LABELS[key];
  const text = key.replace(/_/g, " ").trim();
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : "Verification check";
}

function renderChecks(name, checks) {
  const list = tools.get(name).checkList;
  const entries = Object.entries({ ...checks, browser_font_loaded: true });
  list.replaceChildren();
  for (const [key, passed] of entries) {
    const row = document.createElement("li");
    row.className = passed === true ? "is-pass" : "is-fail";
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    row.append(dot, document.createTextNode(readableCheck(key)));
    list.append(row);
  }
}

function axisNumber(value) {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
}

function applyAxisValues(name) {
  const values = axisValues.get(name);
  if (!values) return;
  tools.get(name).specimen.style.fontVariationSettings = Array.from(values.entries())
    .map(([tag, value]) => `"${tag}" ${value}`)
    .join(", ");
}

function renderAxes(name, axes = []) {
  const panel = tools.get(name).axisPanel;
  if (!panel) return;
  panel.replaceChildren();
  if (!axes.length) {
    panel.hidden = true;
    axisValues.delete(name);
    return;
  }

  const values = new Map(axes.map((axis) => [axis.tag, axis.default]));
  axisValues.set(name, values);
  const heading = document.createElement("div");
  heading.className = "axis-lab-heading";
  const eyebrow = document.createElement("span");
  eyebrow.textContent = "LIVE DESIGNSPACE";
  const summary = document.createElement("strong");
  summary.textContent = `${axes.length} registered ${axes.length === 1 ? "axis" : "axes"} · drag to inspect the built continuum`;
  heading.append(eyebrow, summary);
  panel.append(heading);

  for (const axis of axes) {
    const row = document.createElement("div");
    row.className = "axis-row";
    const label = document.createElement("label");
    const controlId = `${name}-axis-${axis.tag}`;
    label.htmlFor = controlId;
    const tag = document.createElement("code");
    tag.textContent = axis.tag;
    const axisName = document.createElement("span");
    axisName.textContent = axis.name;
    const output = document.createElement("output");
    output.setAttribute("for", controlId);
    output.value = axisNumber(axis.default);
    label.append(tag, axisName, output);

    const range = document.createElement("input");
    range.id = controlId;
    range.type = "range";
    range.min = String(axis.min);
    range.max = String(axis.max);
    range.step = axis.tag === "wght" ? "1" : "0.1";
    range.value = String(axis.default);
    range.disabled = axis.min === axis.max;
    range.setAttribute("aria-label", `${axis.name} axis`);
    range.addEventListener("input", () => {
      const value = Number(range.value);
      values.set(axis.tag, value);
      output.value = axisNumber(value);
      applyAxisValues(name);
    });

    const bounds = document.createElement("div");
    bounds.className = "axis-bounds";
    for (const [label, value] of [["MIN", axis.min], ["DEFAULT", axis.default], ["MAX", axis.max]]) {
      const bound = document.createElement("span");
      bound.textContent = `${label} ${axisNumber(value)}`;
      bounds.append(bound);
    }
    row.append(label, range, bounds);
    panel.append(row);
  }
  panel.hidden = false;
  applyAxisValues(name);
}

function configureObliqueResult(isSlantVariable) {
  const machine = tools.get("oblique").machine;
  const replacements = isSlantVariable ? {
    "[data-success-copy]": "Variable slant package ready",
    "[data-native-title]": "Slant-axis TTF",
    "[data-native-note]": "Upright to verified Oblique endpoint",
    "[data-web-title]": "Slant-axis WOFF2",
    "[data-css-note]": "Registered slant range, generic family",
    "[data-bundle-title]": "Take the slant-axis set"
  } : {
    "[data-success-copy]": "Mechanical Oblique package ready",
    "[data-native-title]": "Oblique font",
    "[data-native-note]": "Mechanical slant, locally checked",
    "[data-web-title]": "Oblique WOFF2",
    "[data-css-note]": "Oblique style, generic family name",
    "[data-bundle-title]": "Take the Oblique set"
  };
  for (const [selector, copy] of Object.entries(replacements)) {
    const node = machine.querySelector(selector);
    if (node) node.textContent = copy;
  }
}

async function acceptResult(name, data, context) {
  assertPublicResult(data);
  activeJobs.set(name, data.job);
  try {
    await loadPreview(name, data.web.url);
  } catch (error) {
    await purge(name);
    throw error;
  }

  const ui = tools.get(name);
  for (const kind of ["native", "web", "css", "bundle"]) {
    ui.machine.querySelector(`[data-download="${kind}"]`).href = localPath(data[kind].url);
  }
  renderChecks(name, data.checks);
  renderAxes(name, data.axes || []);

  const resultContext = ui.machine.querySelector("[data-result-context]");
  if (resultContext && context) resultContext.textContent = context;
  setView(name, "result");
  ui.specimen.focus({ preventScroll: true });
}

async function processSingle(name, file, endpoint, extraHeaders = {}) {
  if (!file || file.size === 0) {
    const message = name === "oblique"
      ? "Choose one non-empty standalone TTF. No output was kept."
      : "Choose one non-empty TTF or OTF. No output was kept.";
    fail(name, message);
    return;
  }
  if (file.size > MAX_FONT_BYTES) {
    fail(name, "This font exceeds the 128 MB local limit. No output was kept.");
    return;
  }

  setView(name, "processing");
  let buffer = null;
  try {
    buffer = await file.arrayBuffer();
    file = null;
    const validFont = name === "oblique" ? looksLikeTrueType(buffer) : looksLikeOpenType(buffer);
    if (!validFont) {
      const message = name === "oblique"
        ? "Oblique Lab accepts standalone TrueType fonts only. No output was kept."
        : "That is not a standalone TTF or OTF font. No output was kept.";
      throw new SafeMessage(message);
    }
    const data = await postLocal(endpoint, {
      "Content-Type": "application/octet-stream",
      ...extraHeaders
    }, buffer);
    wipe(buffer);
    buffer = null;
    const isSlantVariable = name === "oblique" && Array.isArray(data.axes) && data.axes.some((axis) => axis.tag === "slnt");
    if (name === "oblique") configureObliqueResult(isSlantVariable);
    const context = name === "oblique"
      ? isSlantVariable
        ? `Built a live 0° to ${extraHeaders["X-FontBlind-Angle"]}° mechanical slant range. Still Oblique, never a designed Italic.`
        : `Built at ${extraHeaders["X-FontBlind-Angle"]}°. This is an Oblique, not a designed Italic.`
      : null;
    await acceptResult(name, data, context);
  } catch (error) {
    wipe(buffer);
    buffer = null;
    fail(name, error instanceof SafeMessage && error.message ? error.message : DEFAULT_ERRORS[name]);
  }
}

async function processVariable(files) {
  const name = "variable";
  if (files.length < 2 || files.length > 12) {
    files.length = 0;
    fail(name, "Choose 2–12 compatible TTF donors in one drop. No font was kept.");
    return;
  }
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (files.some((file) => file.size === 0)) {
    files.length = 0;
    fail(name, "Every donor must be a non-empty standalone TTF. No font was kept.");
    return;
  }
  if (totalBytes > MAX_VARIABLE_BYTES) {
    files.length = 0;
    fail(name, "This donor set exceeds the 256 MB local limit. No font was kept.");
    return;
  }

  const donorCount = files.length;
  setView(name, "processing");
  const processContext = tools.get(name).machine.querySelector("[data-process-context]");
  processContext.textContent = `${donorCount} anonymous donors loaded. Weight and width coordinates come from font structure.`;

  let encoded = [];
  let body = null;
  try {
    for (const file of files) {
      const buffer = await file.arrayBuffer();
      if (!looksLikeTrueType(buffer)) {
        wipe(buffer);
        throw new SafeMessage("Variable Lab accepts standalone TrueType donors only. No font was kept.");
      }
      encoded.push(bytesToBase64(buffer));
      wipe(buffer);
    }
    files.length = 0;
    body = JSON.stringify({ fonts: encoded });
    encoded = [];
    const data = await postLocal("/api/lab/variable", { "Content-Type": "application/json" }, body);
    body = null;
    const axisCopy = (data.axes || []).map((axis) => axis.tag).join(" + ");
    await acceptResult(name, data, `${donorCount} real donor masters joined into ${axisCopy || "a verified"} system.`);
  } catch (error) {
    files.length = 0;
    encoded = [];
    body = null;
    fail(name, error instanceof SafeMessage && error.message ? error.message : DEFAULT_ERRORS[name]);
  }
}

function currentAngle() {
  const ui = tools.get("oblique");
  const number = ui.machine.querySelector("[data-angle-number]");
  const value = Number(number.value);
  return Math.min(20, Math.max(4, Number.isFinite(value) ? value : 12));
}

function currentObliqueOutput() {
  const selected = tools.get("oblique").machine.querySelector("[data-oblique-output]:checked");
  return selected && selected.value === "slnt" ? "slnt" : "static";
}

function syncAngle(raw) {
  const ui = tools.get("oblique");
  const range = ui.machine.querySelector("[data-angle-range]");
  const number = ui.machine.querySelector("[data-angle-number]");
  const output = ui.machine.querySelector("[data-angle-output]");
  const value = Math.min(20, Math.max(4, Number(raw) || 12));
  range.value = String(value);
  number.value = String(value);
  output.value = String(value);
  ui.machine.style.setProperty("--slant-angle", `${-value}deg`);
}

function handleFiles(name, files) {
  if (name === "variable") {
    void processVariable(files);
    return;
  }
  const file = files[0] || null;
  files.length = 0;
  if (name === "oblique") {
    const angle = currentAngle();
    syncAngle(angle);
    void processSingle(name, file, "/api/lab/oblique", {
      "X-FontBlind-Angle": String(angle),
      "X-FontBlind-Output": currentObliqueOutput()
    });
    return;
  }
  void processSingle(name, file, "/api/process");
}

for (const [name, ui] of tools) {
  ui.dropzone.addEventListener("click", () => ui.input.click());
  ui.input.addEventListener("change", () => {
    const files = Array.from(ui.input.files || []);
    ui.input.value = "";
    handleFiles(name, files);
  });

  for (const eventName of ["dragenter", "dragover"]) {
    ui.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      ui.dropzone.classList.add("is-over");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    ui.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      ui.dropzone.classList.remove("is-over");
    });
  }
  ui.dropzone.addEventListener("drop", (event) => {
    handleFiles(name, Array.from(event.dataTransfer.files || []));
  });

  ui.machine.querySelectorAll("[data-reset]").forEach((button) => {
    button.addEventListener("click", async () => {
      await purge(name);
      setView(name, "drop");
      ui.dropzone.focus();
    });
  });
}

const angleRange = tools.get("oblique").machine.querySelector("[data-angle-range]");
const angleNumber = tools.get("oblique").machine.querySelector("[data-angle-number]");
angleRange.addEventListener("input", () => syncAngle(angleRange.value));
angleNumber.addEventListener("input", () => {
  const value = Number(angleNumber.value);
  if (Number.isFinite(value) && value >= 4 && value <= 20) syncAngle(value);
});
angleNumber.addEventListener("change", () => syncAngle(angleNumber.value));

function switchWorkspace(name, updateHash = true) {
  if (!TOOL_ORDER.includes(name)) return;
  document.body.dataset.activeTool = name;
  document.title = TOOL_TITLES[name];
  document.querySelectorAll("[data-workspace]").forEach((workspace) => {
    workspace.hidden = workspace.dataset.workspace !== name;
  });
  document.querySelectorAll("[data-workspace-target]").forEach((button) => {
    const current = button.dataset.workspaceTarget === name;
    button.classList.toggle("is-current", current);
    if (button.classList.contains("tool-link")) {
      if (current) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
  });
  if (updateHash && window.location.hash !== `#${name}`) {
    window.history.replaceState(null, "", `#${name}`);
  }
}

document.querySelectorAll("[data-workspace-target]").forEach((button) => {
  button.addEventListener("click", () => switchWorkspace(button.dataset.workspaceTarget));
});

const toolLinks = Array.from(document.querySelectorAll(".tool-link"));
toolLinks.forEach((button, index) => {
  button.addEventListener("keydown", (event) => {
    if (!(["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowLeft") next = (index - 1 + toolLinks.length) % toolLinks.length;
    if (event.key === "ArrowRight") next = (index + 1) % toolLinks.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = toolLinks.length - 1;
    toolLinks[next].focus();
    switchWorkspace(toolLinks[next].dataset.workspaceTarget);
  });
});

window.addEventListener("hashchange", () => {
  const name = window.location.hash.slice(1);
  if (TOOL_ORDER.includes(name)) switchWorkspace(name, false);
});

window.addEventListener("pagehide", () => {
  for (const token of activeJobs.values()) {
    fetch(`/api/jobs/${token}`, {
      method: "DELETE",
      headers: { "X-FontBlind-Session": sessionSecret || "" },
      keepalive: true,
      cache: "no-store",
      credentials: "same-origin"
    }).catch(() => {});
  }
});

for (const name of TOOL_ORDER) setView(name, "drop");
syncAngle(12);
const initialWorkspace = window.location.hash.slice(1);
switchWorkspace(TOOL_ORDER.includes(initialWorkspace) ? initialWorkspace : "blind", false);
