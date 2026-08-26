"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { formatLocation, validateStaticResult } = require("../web/instance-export.js");

function result() {
  const token = "a".repeat(32);
  return {
    ok: true,
    job: token,
    native: { url: `/download/${token}/native` },
    web: { url: `/download/${token}/web` },
    css: { url: `/download/${token}/css` },
    bundle: { url: `/download/${token}/bundle` },
    checks: {
      source_identity_removed: true,
      embedding_flags_cleared: true,
      selected_location_verified: true,
      static_instance_verified: true,
      variation_tables_removed: true,
      axis_metadata_verified: true,
      harfbuzz_shaping_verified: true,
      woff2_roundtrip_verified: true,
      source_discarded: true
    }
  };
}

test("formats generated coordinates without source labels", () => {
  assert.equal(
    formatLocation(
      [{ tag: "wght" }, { tag: "wdth" }],
      { wght: 525, wdth: 87.5 }
    ),
    "wght 525 · wdth 87.5"
  );
});

test("accepts a complete local static proof", () => {
  assert.equal(validateStaticResult(result(), "http://127.0.0.1:7331").ok, true);
});

test("rejects non-local downloads and residual axes", () => {
  const external = result();
  external.web.url = "https://example.com/font.woff2";
  assert.throws(() => validateStaticResult(external, "http://127.0.0.1:7331"), /non-local/);

  const variable = result();
  variable.axes = [];
  assert.throws(() => validateStaticResult(variable, "http://127.0.0.1:7331"), /remained variable/);
});

test("rejects missing, failed, and invented proof claims", () => {
  const missing = result();
  delete missing.checks.static_instance_verified;
  assert.throws(() => validateStaticResult(missing), /omitted/);

  const failed = result();
  failed.checks.woff2_roundtrip_verified = false;
  assert.throws(() => validateStaticResult(failed), /failed or unrecognised/);

  const invented = result();
  invented.checks.looks_good_to_me = true;
  assert.throws(() => validateStaticResult(invented), /failed or unrecognised/);
});
