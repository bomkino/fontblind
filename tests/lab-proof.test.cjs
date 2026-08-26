"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const proof = require("../web/lab-proof.js");

const weight = { tag: "wght", name: "Weight", min: 400, default: 400, max: 700 };
const width = { tag: "wdth", name: "Width", min: 75, default: 100, max: 125 };

function masters2d() {
  return [
    { id: "M01", default: false, location: { wght: 400, wdth: 75 } },
    { id: "M02", default: true, location: { wght: 400, wdth: 100 } },
    { id: "M03", default: false, location: { wght: 400, wdth: 125 } },
    { id: "M04", default: false, location: { wght: 700, wdth: 100 } }
  ];
}

test("one-axis proofing creates min, midpoint, and merged default/max endpoints", () => {
  const slant = { tag: "slnt", name: "Slant", min: -12, default: 0, max: 0 };
  assert.deepEqual(proof.axisSamples(slant), [
    { value: -12, roles: ["min"] },
    { value: -6, roles: ["mid"] },
    { value: 0, roles: ["default", "max"] }
  ]);
  const points = proof.buildLocations(slant ? [slant] : [], [
    { id: "M01", default: false, location: { slnt: -12 } },
    { id: "M02", default: true, location: { slnt: 0 } }
  ]);
  assert.equal(points.length, 3);
  assert.equal(points[0].masterId, "M01");
  assert.equal(points[1].isMaster, false);
  assert.equal(points[2].masterId, "M02");
  assert.equal(points[2].isDefault, true);
});

test("two-axis proofing builds a stable nine-point cross-product", () => {
  const points = proof.buildLocations([weight, width], masters2d());
  assert.equal(points.length, 9);
  assert.deepEqual(points[0].location, { wght: 400, wdth: 125 });
  assert.deepEqual(points[4].location, { wght: 550, wdth: 100 });
  assert.deepEqual(points[8].location, { wght: 700, wdth: 75 });
  assert.equal(points.filter((point) => point.isMaster).length, 4);
  assert.equal(points.filter((point) => point.isDefault).length, 1);
  assert.equal(points.find((point) => point.isDefault).masterId, "M02");
});

test("proof locations expose only anonymous functional data", () => {
  const points = proof.buildLocations([weight, width], masters2d());
  const rendered = JSON.stringify(points).toLowerCase();
  assert.doesNotMatch(rendered, /filename|family|source|path|hash/);
  assert.match(rendered, /m01/);
  assert.equal(proof.settingsFor(points[4].location, [weight, width]), '"wght" 550, "wdth" 100');
});

test("invalid or ambiguous designspaces fail closed", () => {
  assert.throws(() => proof.buildLocations([], []), /one or two axes/);
  assert.throws(() => proof.buildLocations([weight, weight], masters2d()), /unique/);
  assert.throws(() => proof.buildLocations([weight], [
    { id: "M01", default: false, location: { wght: 400 } },
    { id: "M02", default: false, location: { wght: 700 } }
  ]), /one default/);
  assert.throws(() => proof.buildLocations([weight], [
    { id: "M01", default: true, location: { wght: 900 } },
    { id: "M02", default: false, location: { wght: 700 } }
  ]), /coordinate/);
});
