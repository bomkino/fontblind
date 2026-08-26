"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { formatLocation, validateStaticResult } = require("../web/instance-export.js");

function result() {
  const token = "a".repeat(32);
  return {
    ok: true,
    job: token,
    location: { wght: 550 },
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

test("accepts a complete local static proof at the requested location", () => {
  const value = validateStaticResult(result(), "http://127.0.0.1:7331", { wght: 550 });
  assert.equal(value.ok, true);
  assert.deepEqual(value.location, { wght: 550 });
});

test("rejects non-local downloads and residual axes", () => {
  const external = result();
  external.web.url = "https://example.com/font.woff2";
  assert.throws(() => validateStaticResult(external, "http://127.0.0.1:7331"), /non-local/);

  const variable = result();
  variable.axes = [];
  assert.throws(() => validateStaticResult(variable, "http://127.0.0.1:7331"), /remained variable/);
});

test("rejects missing, failed, invented, and extra proof claims", () => {
  const missing = result();
  delete missing.checks.static_instance_verified;
  assert.throws(() => validateStaticResult(missing), /wrong verification contract/);

  const failed = result();
  failed.checks.woff2_roundtrip_verified = false;
  assert.throws(() => validateStaticResult(failed), /wrong verification contract/);

  const invented = result();
  invented.checks.looks_good_to_me = true;
  assert.throws(() => validateStaticResult(invented), /wrong verification contract/);

  const extraKnown = result();
  extraKnown.checks.weight_axis_verified = true;
  assert.throws(() => validateStaticResult(extraKnown), /wrong verification contract/);
});

test("rejects absent, malformed, or mismatched server-confirmed locations", () => {
  const absent = result();
  delete absent.location;
  assert.throws(() => validateStaticResult(absent), /no verified location/);

  const boolean = result();
  boolean.location.wght = true;
  assert.throws(() => validateStaticResult(boolean), /invalid generated-axis coordinate/);

  const unexpectedAxis = result();
  unexpectedAxis.location = { opsz: 12 };
  assert.throws(() => validateStaticResult(unexpectedAxis), /invalid generated-axis location/);

  assert.throws(
    () => validateStaticResult(result(), "http://127.0.0.1:7331", { wght: 551 }),
    /different generated-axis location/
  );
});
