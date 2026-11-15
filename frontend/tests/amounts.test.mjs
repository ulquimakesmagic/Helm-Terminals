import assert from "node:assert/strict";
import test from "node:test";
import { formatRaw } from "../src/amounts.ts";

test("money displays preserve integer base units beyond JavaScript number precision", () => {
  assert.equal(formatRaw("1000000000000000000000000007", 18, 18), "1,000,000,000.000000000000000007");
  assert.equal(formatRaw("6000000", 6), "6");
  assert.equal(formatRaw("1001", 3), "1.001");
  assert.equal(formatRaw("1", 9), "<0.000001");
  assert.equal(formatRaw("0", 18), "0");
});

test("invalid amounts cannot silently display as a zero payment", () => {
  for (const raw of ["NaN", "Infinity", "1.5", "", "1e18"]) assert.equal(formatRaw(raw, 6), "Unavailable");
  assert.equal(formatRaw("10", -1), "Unavailable");
  assert.equal(formatRaw("10", 78), "Unavailable");
});
