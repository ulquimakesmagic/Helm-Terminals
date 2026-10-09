import assert from "node:assert/strict";
import test from "node:test";
import { groupedFields, validateLaunchFields } from "../src/launchpadForms.ts";

test("invalid numeric input and unsupported selections cannot produce a valid draft", () => {
  const number = [{ key: "dev_buy_sol", type: "number", min: 0, max: 0 }];
  for (const value of ["", null, undefined, NaN, Infinity, -1, 1]) assert.ok(validateLaunchFields(number, { dev_buy_sol: value }));
  assert.equal(validateLaunchFields(number, { dev_buy_sol: "0" }), null);
  assert.ok(validateLaunchFields([{ key: "tax", type: "select", options: [0, 1, 3] }], { tax: 5 }));
});
test("grouping retains unrecognized provider fields once and does not mix creator funding into settings", () => {
  const fields = ["sniper_decay_s", "future_setting", "dev_buy_sol", "slippage_pct", "sniper_start_pct"].map(key => ({ key, type: "number" }));
  const groups = groupedFields("clanker", fields);
  assert.deepEqual(groups[0].fields.map(f => f.key), ["sniper_start_pct", "sniper_decay_s"]);
  assert.deepEqual(groups.flatMap(g => g.fields.map(f => f.key)).sort(), fields.filter(f => f.key !== "dev_buy_sol").map(f => f.key).sort());
});
