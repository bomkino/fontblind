"use strict";

(function exposeResultContract(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.FontBlindResultContract = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const AXIS_NAMES = Object.freeze({ wght: "Weight", wdth: "Width", slnt: "Slant" });
  const SAFE_FILENAME = /^[a-z0-9][a-z0-9._-]{0,127}$/;
  const OUTPUTS = Object.freeze({
    native: Object.freeze({ media: new Set(["font/ttf", "font/otf"]), suffix: new Set([".ttf", ".otf"]) }),
    web: Object.freeze({ media: new Set(["font/woff2"]), suffix: new Set([".woff2"]) }),
    css: Object.freeze({ media: new Set(["text/css; charset=utf-8"]), suffix: new Set([".css"]) }),
    bundle: Object.freeze({ media: new Set(["application/zip"]), suffix: new Set([".zip"]) })
  });
  const BASE_CHECKS = Object.freeze({
    blind: Object.freeze([
      "source_identity_removed",
      "embedding_flags_cleared",
      "outline_flavor_retained",
      "functional_clone_verified",
      "harfbuzz_shaping_verified",
      "woff2_roundtrip_verified",
      "source_discarded"
    ]),
    oblique: Object.freeze([
      "source_identity_removed",
      "embedding_flags_cleared",
      "declared_shear_verified",
      "oblique_not_italic_verified",
      "hinting_removed",
      "harfbuzz_shaping_verified",
      "woff2_roundtrip_verified",
      "source_discarded"
    ]),
    slant: Object.freeze([
      "source_identity_removed",
      "embedding_flags_cleared",
      "declared_shear_verified",
      "slant_axis_verified",
      "variable_endpoints_verified",
      "oblique_not_italic_verified",
      "hinting_removed",
      "harfbuzz_shaping_verified",
      "woff2_roundtrip_verified",
      "source_discarded"
    ]),
    variable: Object.freeze([
      "source_identity_removed",
      "embedding_flags_cleared",
      "donor_compatibility_verified",
      "donor_instances_verified",
      "independent_axis_model_verified",
      "axis_metadata_verified",
      "hinting_removed",
      "harfbuzz_shaping_verified",
      "woff2_roundtrip_verified",
      "source_discarded"
    ])
  });

  class ResultContractError extends TypeError {}

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value);
  }

  function exactKeys(value, expected, context) {
    if (!object(value)) throw new ResultContractError(`${context} must be an object`);
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
      throw new ResultContractError(`${context} returned unexpected fields`);
    }
  }

  function finite(value, context) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new ResultContractError(`${context} must be a finite number`);
    }
    return value;
  }

  function suffix(filename) {
    const index = filename.lastIndexOf(".");
    return index < 0 ? "" : filename.slice(index).toLowerCase();
  }

  function validateOutputs(data, origin) {
    const filenames = new Set();
    for (const [kind, spec] of Object.entries(OUTPUTS)) {
      const item = data[kind];
      exactKeys(item, ["kind", "filename", "media_type", "url"], `${kind} output`);
      if (item.kind !== kind || typeof item.filename !== "string" || !SAFE_FILENAME.test(item.filename) ||
          item.filename.includes("/") || item.filename.includes("\\") || item.filename.includes("\0") ||
          !spec.media.has(item.media_type) || !spec.suffix.has(suffix(item.filename)) || filenames.has(item.filename)) {
        throw new ResultContractError(`invalid ${kind} output descriptor`);
      }
      filenames.add(item.filename);
      if (kind === "native" &&
          ((item.media_type === "font/ttf") !== (suffix(item.filename) === ".ttf"))) {
        throw new ResultContractError("native output media type and suffix disagree");
      }
      let parsed;
      try {
        parsed = new URL(item.url, `${origin}/`);
      } catch (_) {
        throw new ResultContractError(`invalid ${kind} download URL`);
      }
      if (parsed.origin !== origin || parsed.search || parsed.hash ||
          parsed.pathname !== `/download/${data.job}/${kind}`) {
        throw new ResultContractError(`non-local or incoherent ${kind} download URL`);
      }
    }
  }

  function validateAxes(raw) {
    if (raw === undefined) return [];
    if (!Array.isArray(raw) || raw.length < 1 || raw.length > 2) {
      throw new ResultContractError("invalid variation controls");
    }
    const axes = [];
    const tags = new Set();
    for (const [index, axis] of raw.entries()) {
      exactKeys(axis, ["tag", "name", "min", "default", "max"], `axis ${index}`);
      if (!Object.hasOwn(AXIS_NAMES, axis.tag) || tags.has(axis.tag) || axis.name !== AXIS_NAMES[axis.tag]) {
        throw new ResultContractError("invalid or non-neutral variation axis");
      }
      const minimum = finite(axis.min, "axis minimum");
      const defaultValue = finite(axis.default, "axis default");
      const maximum = finite(axis.max, "axis maximum");
      if (!(minimum <= defaultValue && defaultValue <= maximum) || minimum === maximum) {
        throw new ResultContractError("invalid variation bounds");
      }
      tags.add(axis.tag);
      axes.push({ tag: axis.tag, name: axis.name, min: minimum, default: defaultValue, max: maximum });
    }
    return axes;
  }

  function validateMasters(raw, axes) {
    if (raw === undefined) return [];
    if (!axes.length || !Array.isArray(raw) || raw.length < 2 || raw.length > 12) {
      throw new ResultContractError("invalid anonymous master map");
    }
    const tags = axes.map((axis) => axis.tag).sort();
    const ids = new Set();
    const coordinates = new Set();
    let defaultMaster = null;
    const masters = [];
    for (const [index, master] of raw.entries()) {
      exactKeys(master, ["id", "location", "default"], `master ${index}`);
      if (typeof master.id !== "string" || !/^M\d{2}$/.test(master.id) || ids.has(master.id) ||
          typeof master.default !== "boolean") {
        throw new ResultContractError("invalid anonymous master descriptor");
      }
      exactKeys(master.location, tags, `master ${master.id} location`);
      ids.add(master.id);
      const location = {};
      for (const axis of axes) {
        const value = finite(master.location[axis.tag], `master ${master.id} coordinate`);
        if (value < axis.min || value > axis.max) {
          throw new ResultContractError("anonymous master lies outside the generated range");
        }
        location[axis.tag] = value;
      }
      const coordinate = tags.map((tag) => `${tag}:${location[tag]}`).join("|");
      if (coordinates.has(coordinate)) throw new ResultContractError("duplicate anonymous master coordinates");
      coordinates.add(coordinate);
      if (master.default) {
        if (defaultMaster) throw new ResultContractError("multiple anonymous default masters");
        defaultMaster = location;
      }
      masters.push({ id: master.id, location, default: master.default });
    }
    if (!defaultMaster || axes.some((axis) => Math.abs(defaultMaster[axis.tag] - axis.default) > 0.000001)) {
      throw new ResultContractError("anonymous default master does not match axis defaults");
    }
    return masters;
  }

  function laneFor(tool, axes, masters) {
    const tags = axes.map((axis) => axis.tag);
    if (tool === "blind") {
      if (axes.length || masters.length) throw new ResultContractError("Blind returned Lab inspection data");
      return "blind";
    }
    if (tool === "oblique") {
      if (!axes.length && !masters.length) return "oblique";
      if (tags.length === 1 && tags[0] === "slnt" && masters.length === 2) return "slant";
      throw new ResultContractError("Oblique Lab returned the wrong generated model");
    }
    if (tool === "variable") {
      const signature = tags.join("+");
      if (!["wght", "wdth", "wght+wdth"].includes(signature) || masters.length < 2) {
        throw new ResultContractError("Variable Lab returned the wrong generated model");
      }
      return "variable";
    }
    throw new ResultContractError("unknown FontBlind workbench");
  }

  function expectedChecks(lane, axes) {
    const checks = new Set(BASE_CHECKS[lane]);
    if (lane === "variable") {
      const tags = new Set(axes.map((axis) => axis.tag));
      if (tags.has("wght")) checks.add("weight_axis_verified");
      if (tags.has("wdth")) checks.add("width_axis_verified");
    }
    return checks;
  }

  function validateChecks(raw, expected) {
    if (!object(raw)) throw new ResultContractError("result omitted verification proof");
    const keys = Object.keys(raw);
    if (keys.length !== expected.size || keys.some((key) => !expected.has(key) || raw[key] !== true) ||
        [...expected].some((key) => raw[key] !== true)) {
      throw new ResultContractError("result returned the wrong verification contract");
    }
  }

  function validate(data, tool, origin = "http://127.0.0.1") {
    if (!object(data) || data.ok !== true || typeof data.job !== "string" || !/^[a-f0-9]{32}$/.test(data.job)) {
      throw new ResultContractError("incomplete FontBlind result");
    }
    const axesPresent = data.axes !== undefined;
    const mastersPresent = data.masters !== undefined;
    const fields = ["ok", "job", "native", "web", "css", "bundle", "checks"];
    if (axesPresent) fields.push("axes");
    if (mastersPresent) fields.push("masters");
    exactKeys(data, fields, "FontBlind result");

    validateOutputs(data, origin);
    const axes = validateAxes(data.axes);
    const masters = validateMasters(data.masters, axes);
    if (axes.length && !masters.length) throw new ResultContractError("generated axes have no anonymous master map");
    if (!axes.length && masters.length) throw new ResultContractError("anonymous masters have no generated axes");
    const lane = laneFor(tool, axes, masters);
    validateChecks(data.checks, expectedChecks(lane, axes));
    return Object.freeze({ lane, axes, masters });
  }

  return Object.freeze({ ResultContractError, validate });
});
