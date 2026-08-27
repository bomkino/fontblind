"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  DesktopRuntimeError,
  validateSessionEnvelope,
  validateShutdownEnvelope,
  waitForShutdown
} = require("../web/desktop-runtime.js");

test("accepts only the exact desktop capability envelope", () => {
  const value = validateSessionEnvelope({ ok: true, session: "local-secret", can_quit: true });
  assert.deepEqual(value, { session: "local-secret", canQuit: true });
  assert.throws(
    () => validateSessionEnvelope({ ok: true, session: "local-secret", can_quit: true, source: "/private/font.ttf" }),
    DesktopRuntimeError
  );
  assert.throws(() => validateSessionEnvelope({ ok: true, session: "", can_quit: true }), DesktopRuntimeError);
  assert.throws(() => validateSessionEnvelope({ ok: true, session: "local-secret", can_quit: 1 }), DesktopRuntimeError);
});

test("requires an exact shutdown acknowledgement", () => {
  assert.equal(validateShutdownEnvelope({ ok: true, shutdown: true }), true);
  assert.throws(() => validateShutdownEnvelope({ ok: true, shutdown: false }), DesktopRuntimeError);
  assert.throws(() => validateShutdownEnvelope({ ok: true, shutdown: true, path: "/tmp/source.ttf" }), DesktopRuntimeError);
});


test("does not claim closure until the local service actually disappears", async () => {
  let calls = 0;
  const closed = await waitForShutdown(async () => {
    calls += 1;
    if (calls < 3) return { ok: true };
    throw new Error("connection refused");
  }, 5);
  assert.equal(closed, true);
  assert.equal(calls, 3);

  assert.equal(await waitForShutdown(async () => ({ ok: true }), 2), false);
  await assert.rejects(() => waitForShutdown(null, 2), DesktopRuntimeError);
});
