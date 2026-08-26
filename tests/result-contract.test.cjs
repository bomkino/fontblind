"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  installFetchGuard,
  validate
} = require("../web/result-contract.js");

const ORIGIN = "http://127.0.0.1:7331";
const TOKEN = "a".repeat(32);

const CHECKS = {
  blind: [
    "source_identity_removed",
    "embedding_flags_cleared",
    "outline_flavor_retained",
    "functional_clone_verified",
    "harfbuzz_shaping_verified",
    "woff2_roundtrip_verified",
    "source_discarded"
  ],
  oblique: [
    "source_identity_removed",
    "embedding_flags_cleared",
    "declared_shear_verified",
    "oblique_not_italic_verified",
    "hinting_removed",
    "harfbuzz_shaping_verified",
    "woff2_roundtrip_verified",
    "source_discarded"
  ],
  slant: [
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
  ],
  variable: [
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
  ]
};

function output(kind, filename, mediaType) {
  return {
    kind,
    filename,
    media_type: mediaType,
    url: `/download/${TOKEN}/${kind}`
  };
}

function checks(keys) {
  return Object.fromEntries(keys.map((key) => [key, true]));
}

function baseResult(keys = CHECKS.blind) {
  return {
    ok: true,
    job: TOKEN,
    native: output("native", "fontblind-native.ttf", "font/ttf"),
    web: output("web", "fontblind-web.woff2", "font/woff2"),
    css: output("css", "fontblind.css", "text/css; charset=utf-8"),
    bundle: output("bundle", "fontblind-package.zip", "application/zip"),
    checks: checks(keys)
  };
}

function slantResult() {
  return {
    ...baseResult(CHECKS.slant),
    axes: [{ tag: "slnt", name: "Slant", min: -12, default: 0, max: 0 }],
    masters: [
      { id: "M01", location: { slnt: 0 }, default: true },
      { id: "M02", location: { slnt: -12 }, default: false }
    ]
  };
}

function variableResult() {
  return {
    ...baseResult([...CHECKS.variable, "weight_axis_verified", "width_axis_verified"]),
    axes: [
      { tag: "wght", name: "Weight", min: 400, default: 400, max: 700 },
      { tag: "wdth", name: "Width", min: 75, default: 100, max: 100 }
    ],
    masters: [
      { id: "M01", location: { wght: 400, wdth: 100 }, default: true },
      { id: "M02", location: { wght: 700, wdth: 100 }, default: false },
      { id: "M03", location: { wght: 400, wdth: 75 }, default: false }
    ]
  };
}

test("accepts only the exact Blind, Oblique, slant, and Variable contracts", () => {
  assert.equal(validate(baseResult(), "blind", ORIGIN).lane, "blind");
  assert.equal(validate(baseResult(CHECKS.oblique), "oblique", ORIGIN).lane, "oblique");
  assert.equal(validate(slantResult(), "oblique", ORIGIN).lane, "slant");
  const variable = validate(variableResult(), "variable", ORIGIN);
  assert.equal(variable.lane, "variable");
  assert.deepEqual(variable.axes.map((axis) => axis.tag), ["wght", "wdth"]);
});

test("rejects omitted, failed, invented, and cross-lane proof", () => {
  const missing = baseResult();
  delete missing.checks.functional_clone_verified;
  assert.throws(() => validate(missing, "blind", ORIGIN), /wrong verification contract/);

  const failed = baseResult();
  failed.checks.woff2_roundtrip_verified = false;
  assert.throws(() => validate(failed, "blind", ORIGIN), /wrong verification contract/);

  const invented = baseResult();
  invented.checks.looks_fine = true;
  assert.throws(() => validate(invented, "blind", ORIGIN), /wrong verification contract/);

  assert.throws(() => validate(slantResult(), "variable", ORIGIN), /wrong generated model/);
});

test("rejects extra public fields and incoherent download descriptors", () => {
  const sourceField = baseResult();
  sourceField.source = "/private/source.ttf";
  assert.throws(() => validate(sourceField, "blind", ORIGIN), /unexpected fields/);

  const external = baseResult();
  external.web.url = "https://example.invalid/font.woff2";
  assert.throws(() => validate(external, "blind", ORIGIN), /non-local/);

  const query = baseResult();
  query.css.url += "?source=hidden";
  assert.throws(() => validate(query, "blind", ORIGIN), /non-local|incoherent/);

  const traversal = baseResult();
  traversal.native.filename = "../font.ttf";
  assert.throws(() => validate(traversal, "blind", ORIGIN), /invalid native/);

  const wrongMedia = baseResult();
  wrongMedia.bundle.media_type = "text/plain";
  assert.throws(() => validate(wrongMedia, "blind", ORIGIN), /invalid bundle/);
});

test("rejects duplicate masters and a default away from axis defaults", () => {
  const duplicate = variableResult();
  duplicate.masters[2].location = { wght: 700, wdth: 100 };
  assert.throws(() => validate(duplicate, "variable", ORIGIN), /duplicate anonymous master/);

  const wrongDefault = variableResult();
  wrongDefault.masters[0].default = false;
  wrongDefault.masters[1].default = true;
  assert.throws(() => validate(wrongDefault, "variable", ORIGIN), /default master/);

  const missingMasters = variableResult();
  delete missingMasters.masters;
  assert.throws(() => validate(missingMasters, "variable", ORIGIN), /no anonymous master map/);
});

test("fetch guard replaces malformed success and requests local cleanup", async () => {
  const malformed = baseResult();
  delete malformed.checks.source_discarded;
  let deleted = false;
  const fakeRoot = {
    document: {},
    location: { href: `${ORIGIN}/`, origin: ORIGIN },
    Headers,
    Response,
    fetch: async (input, init = {}) => {
      const method = String(init.method || "GET").toUpperCase();
      if (method === "DELETE") {
        deleted = true;
        return new Response(JSON.stringify({ ok: true, deleted: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(JSON.stringify(malformed), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
  };

  installFetchGuard(fakeRoot);
  const response = await fakeRoot.fetch("/api/process", {
    method: "POST",
    headers: { "X-FontBlind-Session": "local-session" }
  });
  assert.equal(response.status, 502);
  assert.equal((await response.json()).ok, false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(deleted, true);
});
