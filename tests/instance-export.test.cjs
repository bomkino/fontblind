"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  createOperationLedger,
  formatLocation,
  installErrorFirewall,
  localPath,
  safeErrorMessage,
  sameLocation,
  validateStaticResult
} = require("../web/instance-export.js");

function output(token, kind, filename, mediaType) {
  return {
    kind,
    filename,
    media_type: mediaType,
    url: `/download/${token}/${kind}`
  };
}

function result() {
  const token = "a".repeat(32);
  return {
    ok: true,
    job: token,
    location: { wght: 550 },
    native: output(token, "native", "fontblind-instance.ttf", "font/ttf"),
    web: output(token, "web", "fontblind-instance.woff2", "font/woff2"),
    css: output(token, "css", "fontblind-instance.css", "text/css; charset=utf-8"),
    bundle: output(token, "bundle", "fontblind-instance-package.zip", "application/zip"),
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
      { wght: 537.375, wdth: 93.625 }
    ),
    "wght 537.375 · wdth 93.625"
  );
});

test("accepts only the exact local static package at the requested location", () => {
  const value = validateStaticResult(result(), "http://127.0.0.1:7331", { wght: 550 });
  assert.equal(value.ok, true);
  assert.deepEqual(value.location, { wght: 550 });
});

test("rejects non-local, query-bearing, incoherent, and identity-bearing descriptors", () => {
  const external = result();
  external.web.url = "https://example.com/font.woff2";
  assert.throws(() => validateStaticResult(external, "http://127.0.0.1:7331"), /non-local or incoherent/);

  const query = result();
  query.web.url += "?source=PrivateFamily";
  assert.throws(() => validateStaticResult(query, "http://127.0.0.1:7331"), /non-local or incoherent/);

  const wrongName = result();
  wrongName.native.filename = "PrivateFamily-Regular.ttf";
  assert.throws(() => validateStaticResult(wrongName), /incoherent native descriptor/);

  const extra = result();
  extra.source_filename = "PrivateFamily-Regular.ttf";
  assert.throws(() => validateStaticResult(extra), /unexpected fields/);

  const nestedExtra = result();
  nestedExtra.web.source_path = "/Users/person/PrivateFamily.ttf";
  assert.throws(() => validateStaticResult(nestedExtra), /unexpected fields/);

  assert.throws(
    () => localPath(
      `/download/${"a".repeat(32)}/web#PrivateFamily`,
      "http://127.0.0.1:7331"
    ),
    /non-local or incoherent/
  );
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
  assert.throws(() => validateStaticResult(absent), /unexpected fields/);

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

test("operation ledger makes stale async completions unpublishable", () => {
  const ledger = createOperationLedger();
  const first = ledger.begin("parent-a");
  assert.equal(ledger.current(first), true);

  const second = ledger.begin("parent-a");
  assert.equal(ledger.current(first), false);
  assert.equal(ledger.current(second), true);

  ledger.cancel("parent-a");
  assert.equal(ledger.current(second), false);

  const third = ledger.begin("parent-a");
  ledger.complete(third);
  assert.equal(ledger.current(third), false);
});

test("location comparison invalidates only real coordinate movement", () => {
  const axes = [{ tag: "wght", min: 300, max: 700 }, { tag: "wdth", min: 75, max: 125 }];
  assert.equal(
    sameLocation(axes, { wght: 537.375, wdth: 93.625 }, { wght: 537.3750001, wdth: 93.625 }),
    true
  );
  assert.equal(
    sameLocation(axes, { wght: 537.375, wdth: 93.625 }, { wght: 538, wdth: 93.625 }),
    false
  );
});

test("error firewall preserves reviewed messages and destroys arbitrary server text", async () => {
  const secret = "PrivateFamily-Regular.ttf at /Users/person/PrivateFamily-Regular.ttf";
  const responses = [
    new Response(JSON.stringify({ ok: false, error: secret }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    }),
    new Response(JSON.stringify({
      ok: false,
      error: "This generated position could not be frozen and verified. No output was kept."
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    })
  ];
  const fakeRoot = {
    fetch: async () => responses.shift(),
    location: {
      href: "http://127.0.0.1:7331/",
      origin: "http://127.0.0.1:7331"
    },
    Headers,
    Response
  };

  installErrorFirewall(fakeRoot);
  const rejected = await fakeRoot.fetch("/api/jobs/" + "a".repeat(32) + "/instance", { method: "POST" });
  const rejectedData = await rejected.json();
  assert.equal(rejectedData.error, safeErrorMessage(422, "instance"));
  assert.equal(JSON.stringify(rejectedData).includes("PrivateFamily"), false);
  assert.equal(JSON.stringify(rejectedData).includes("/Users/"), false);

  const reviewed = await fakeRoot.fetch("/api/jobs/" + "a".repeat(32) + "/instance", { method: "POST" });
  const reviewedData = await reviewed.json();
  assert.equal(
    reviewedData.error,
    "This generated position could not be frozen and verified. No output was kept."
  );
});
