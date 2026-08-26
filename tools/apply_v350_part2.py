from __future__ import annotations

import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)
javascript = read("web/app.js")
javascript = replace_once(
    javascript,
    'const MAX_VARIABLE_BYTES = 256 * 1024 * 1024;\n',
    'const MAX_VARIABLE_BYTES = 256 * 1024 * 1024;\n'
    'const FONT_SET_MEDIA_TYPE = "application/vnd.fontblind.font-set";\n'
    'const FONT_SET_MAGIC = new Uint8Array([0x46, 0x42, 0x4c, 0x41, 0x42, 0x31, 0x00, 0x00]);\n',
    label="js protocol constants",
)
javascript = replace_once(
    javascript,
    '''function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 3 * 8192;
  let encoded = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
    encoded += btoa(String.fromCharCode(...chunk));
  }
  return encoded;
}
''',
    '''function buildFontSetBody(files) {
  const header = new Uint8Array(FONT_SET_MAGIC.length + 1 + (files.length * 4));
  header.set(FONT_SET_MAGIC, 0);
  header[FONT_SET_MAGIC.length] = files.length;
  const view = new DataView(header.buffer);
  let offset = FONT_SET_MAGIC.length + 1;
  for (const file of files) {
    view.setUint32(offset, file.size, false);
    offset += 4;
  }
  return new Blob([header, ...files], { type: FONT_SET_MEDIA_TYPE });
}

async function fileLooksLikeTrueType(file) {
  const header = await file.slice(0, 4).arrayBuffer();
  try {
    return looksLikeTrueType(header);
  } finally {
    wipe(header);
  }
}
''',
    label="js binary envelope",
)
assert_start = javascript.index("function assertPublicResult(data) {")
assert_end = javascript.index("\nasync function postLocal", assert_start)
new_assert = r'''function assertPublicResult(data) {
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
  const proof = Object.entries(data.checks);
  if (!proof.length || proof.some(([key, passed]) => typeof key !== "string" || !key || passed !== true)) {
    throw new SafeMessage("The local service returned a failed or malformed proof. Outputs were discarded.");
  }

  const axes = data.axes === undefined ? [] : data.axes;
  if (!Array.isArray(axes) || axes.length > 3) {
    throw new SafeMessage("The local service returned invalid variation controls. No download was exposed.");
  }
  const allowed = new Set(["wght", "wdth", "slnt"]);
  const axisByTag = new Map();
  for (const axis of axes) {
    const values = [axis && axis.min, axis && axis.default, axis && axis.max];
    if (!axis || !allowed.has(axis.tag) || axisByTag.has(axis.tag) || typeof axis.name !== "string" ||
        values.some((value) => typeof value !== "number" || !Number.isFinite(value)) ||
        axis.min > axis.default || axis.default > axis.max) {
      throw new SafeMessage("The local service returned invalid variation controls. No download was exposed.");
    }
    axisByTag.set(axis.tag, axis);
  }

  if (data.masters !== undefined) {
    if (!axes.length || !Array.isArray(data.masters) || data.masters.length < 2 || data.masters.length > 12) {
      throw new SafeMessage("The local service returned an invalid anonymous master map. No download was exposed.");
    }
    const ids = new Set();
    let defaults = 0;
    for (const master of data.masters) {
      if (!master || typeof master !== "object" || Array.isArray(master) ||
          typeof master.id !== "string" || !/^M\d{2}$/.test(master.id) || ids.has(master.id) ||
          typeof master.default !== "boolean" || !master.location ||
          typeof master.location !== "object" || Array.isArray(master.location)) {
        throw new SafeMessage("The local service returned an invalid anonymous master map. No download was exposed.");
      }
      ids.add(master.id);
      defaults += master.default ? 1 : 0;
      const locations = Object.entries(master.location);
      if (locations.length !== axes.length) {
        throw new SafeMessage("The local service returned an incomplete anonymous master map. No download was exposed.");
      }
      for (const [tag, value] of locations) {
        const axis = axisByTag.get(tag);
        if (!axis || typeof value !== "number" || !Number.isFinite(value) ||
            value < axis.min || value > axis.max) {
          throw new SafeMessage("The local service returned an invalid anonymous master location. No download was exposed.");
        }
      }
    }
    if (defaults !== 1) {
      throw new SafeMessage("The local service returned no unique default master. No download was exposed.");
    }
  }
}
'''
javascript = javascript[:assert_start] + new_assert + javascript[assert_end:]

render_start = javascript.index("function renderAxes(name, axes = []) {")
render_end = javascript.index("\nfunction configureObliqueResult", render_start)
new_render = r'''function normalizedAxisValue(axis, value) {
  if (axis.max === axis.min) return 0.5;
  return Math.min(1, Math.max(0, (value - axis.min) / (axis.max - axis.min)));
}

function renderAxes(name, axes = [], masters = []) {
  const panel = tools.get(name).axisPanel;
  if (!panel) return;
  panel.replaceChildren();
  if (!axes.length) {
    panel.hidden = true;
    axisValues.delete(name);
    return;
  }

  const values = new Map(axes.map((axis) => [axis.tag, axis.default]));
  const controls = new Map();
  const pins = new Map();
  axisValues.set(name, values);

  const heading = document.createElement("div");
  heading.className = "axis-lab-heading";
  const eyebrow = document.createElement("span");
  eyebrow.textContent = "LIVE DESIGNSPACE";
  const summary = document.createElement("strong");
  summary.textContent = `${axes.length} registered ${axes.length === 1 ? "axis" : "axes"} · drag to inspect the built continuum`;
  heading.append(eyebrow, summary);
  panel.append(heading);

  function syncPins() {
    for (const [id, entry] of pins) {
      const active = Object.entries(entry.master.location).every(([tag, value]) => {
        const axis = axes.find((item) => item.tag === tag);
        const tolerance = Math.max(0.0001, Math.abs(axis.max - axis.min) / 10000);
        return Math.abs(values.get(tag) - value) <= tolerance;
      });
      entry.button.classList.toggle("is-active", active);
      entry.button.setAttribute("aria-pressed", active ? "true" : "false");
      if (active) entry.button.dataset.activeMaster = id;
      else delete entry.button.dataset.activeMaster;
    }
  }

  function selectMaster(master) {
    for (const [tag, value] of Object.entries(master.location)) {
      values.set(tag, value);
      const control = controls.get(tag);
      if (control) {
        control.range.value = String(value);
        control.output.value = axisNumber(value);
      }
    }
    applyAxisValues(name);
    syncPins();
  }

  if (masters.length) {
    const shell = document.createElement("section");
    shell.className = "master-map-shell";
    shell.setAttribute("aria-label", "Anonymous donor master locations");

    const mapHeading = document.createElement("div");
    mapHeading.className = "master-map-heading";
    const mapTitle = document.createElement("strong");
    mapTitle.textContent = "ANONYMOUS MASTER MAP";
    const mapNote = document.createElement("span");
    mapNote.textContent = "Pins expose functional coordinates only. Source names and paths stay out.";
    mapHeading.append(mapTitle, mapNote);

    const map = document.createElement("div");
    map.className = `master-map is-${axes.length === 1 ? "1d" : "2d"}`;
    const xAxis = axes[0];
    const yAxis = axes[1] || null;

    const xLabel = document.createElement("code");
    xLabel.className = "master-map-axis is-x";
    xLabel.textContent = `${xAxis.tag} →`;
    map.append(xLabel);
    if (yAxis) {
      const yLabel = document.createElement("code");
      yLabel.className = "master-map-axis is-y";
      yLabel.textContent = `${yAxis.tag} →`;
      map.append(yLabel);
    }

    const key = document.createElement("div");
    key.className = "master-map-key";
    for (const master of masters) {
      const x = 6 + (normalizedAxisValue(xAxis, master.location[xAxis.tag]) * 88);
      const y = yAxis
        ? 94 - (normalizedAxisValue(yAxis, master.location[yAxis.tag]) * 88)
        : 50;
      const coordinates = axes
        .map((axis) => `${axis.tag} ${axisNumber(master.location[axis.tag])}`)
        .join(" · ");

      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = `master-pin${master.default ? " is-default" : ""}`;
      pin.style.setProperty("--master-x", `${x}%`);
      pin.style.setProperty("--master-y", `${y}%`);
      pin.textContent = master.id;
      pin.setAttribute("aria-label", `${master.id}: ${coordinates}${master.default ? ", default master" : ""}`);
      pin.setAttribute("aria-pressed", "false");
      pin.addEventListener("click", () => selectMaster(master));
      map.append(pin);
      pins.set(master.id, { button: pin, master });

      const item = document.createElement("span");
      const id = document.createElement("code");
      id.textContent = master.id;
      item.append(id, document.createTextNode(` ${coordinates}${master.default ? " · default" : ""}`));
      key.append(item);
    }
    shell.append(mapHeading, map, key);
    panel.append(shell);
  }

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
      syncPins();
    });
    controls.set(axis.tag, { range, output });

    const bounds = document.createElement("div");
    bounds.className = "axis-bounds";
    for (const [labelText, value] of [["MIN", axis.min], ["DEFAULT", axis.default], ["MAX", axis.max]]) {
      const bound = document.createElement("span");
      bound.textContent = `${labelText} ${axisNumber(value)}`;
      bounds.append(bound);
    }
    row.append(label, range, bounds);
    panel.append(row);
  }
  panel.hidden = false;
  applyAxisValues(name);
  syncPins();
}
'''
javascript = javascript[:render_start] + new_render + javascript[render_end:]
javascript = replace_once(
    javascript,
    '  renderAxes(name, data.axes || []);\n',
    '  renderAxes(name, data.axes || [], data.masters || []);\n',
    label="js render master maps",
)
variable_start = javascript.index("async function processVariable(files) {")
variable_end = javascript.index("\nfunction currentAngle()", variable_start)
new_process_variable = r'''async function processVariable(files) {
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
  if (files.some((file) => file.size > MAX_FONT_BYTES)) {
    files.length = 0;
    fail(name, "A donor exceeds the 128 MB local limit. No font was kept.");
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

  let body = null;
  try {
    for (const file of files) {
      if (!(await fileLooksLikeTrueType(file))) {
        throw new SafeMessage("Variable Lab accepts standalone TrueType donors only. No font was kept.");
      }
    }
    body = buildFontSetBody(files);
    files.length = 0;
    const data = await postLocal(
      "/api/lab/variable",
      { "Content-Type": FONT_SET_MEDIA_TYPE },
      body
    );
    body = null;
    const axisCopy = (data.axes || []).map((axis) => axis.tag).join(" + ");
    const masterCount = Array.isArray(data.masters) ? data.masters.length : donorCount;
    await acceptResult(
      name,
      data,
      `${masterCount} real donor masters joined into ${axisCopy || "a verified"} system. Select a pin to inspect an exact master.`
    );
  } catch (error) {
    files.length = 0;
    body = null;
    fail(name, error instanceof SafeMessage && error.message ? error.message : DEFAULT_ERRORS[name]);
  }
}
'''
javascript = javascript[:variable_start] + new_process_variable + javascript[variable_end:]
javascript = replace_once(
    javascript,
    '''function handleFiles(name, files) {
  if (name === "variable") {
    void processVariable(files);
    return;
  }
  const file = files[0] || null;
''',
    '''function handleFiles(name, files) {
  if (name === "variable") {
    void processVariable(files);
    return;
  }
  if (files.length !== 1) {
    files.length = 0;
    fail(name, name === "oblique"
      ? "Choose exactly one upright TTF. No output was kept."
      : "Choose exactly one TTF or OTF. No output was kept.");
    return;
  }
  const file = files[0] || null;
''',
    label="js exact single input",
)
write("web/app.js", javascript)

index = read("web/index.html")
index = replace_once(
    index,
    '    <link rel="stylesheet" href="/styles.css">\n',
    '    <link rel="stylesheet" href="/styles.css">\n'
    '    <link rel="stylesheet" href="/lab-map.css">\n',
    label="index lab map stylesheet",
)
write("web/index.html", index)
