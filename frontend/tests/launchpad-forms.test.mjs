import assert from "node:assert/strict";
import test from "node:test";
import { groupedFields, validateLaunchFields } from "../src/launchpadForms.ts";

test("auction caps and optional zero hard cap are validated", () => {
  const fields = [{ key: "soft_cap_near", type: "number", min: 1 }, { key: "hard_cap_near", type: "number", min: 0 }];
  assert.equal(validateLaunchFields(fields, { soft_cap_near: 100, hard_cap_near: 0 }), null);
  assert.equal(validateLaunchFields(fields, { soft_cap_near: 100, hard_cap_near: 100 }), null);
  assert.match(validateLaunchFields(fields, { soft_cap_near: 100, hard_cap_near: 50 }), /hard cap/);
});
test("invalid numeric input and unsupported selections cannot produce a valid draft", () => {
  const number = [{ key: "dev_buy_sol", type: "number", min: 0, max: 0 }];
  for (const value of ["", null, undefined, NaN, Infinity, -1, 1]) assert.ok(validateLaunchFields(number, { dev_buy_sol: value }));
  assert.equal(validateLaunchFields(number, { dev_buy_sol: "0" }), null);
  assert.ok(validateLaunchFields([{ key: "tax", type: "select", options: [0, 1, 3] }], { tax: 5 }));
});
test("grouping retains unrecognized provider fields once and does not mix creator funding into settings", () => {
  const fields = ["sell_tax_pct", "future_setting", "dev_buy_sol", "slippage_pct", "buy_tax_pct"].map(key => ({ key, type: "number" }));
  const groups = groupedFields("clanker", fields);
  assert.deepEqual(groups[0].fields.map(f => f.key), ["buy_tax_pct", "sell_tax_pct"]);
  assert.deepEqual(groups.flatMap(g => g.fields.map(f => f.key)).sort(), fields.filter(f => f.key !== "dev_buy_sol").map(f => f.key).sort());
});

test('V5 dependent economics block an invalid draft',()=>{
  assert.match(validateLaunchFields([],{dividend_share_pct:70,burn_share_pct:40}),/cannot exceed/);
  assert.match(validateLaunchFields([],{dividend_share_pct:10,minimum_dividend_tokens:10000.5}),/whole-token/);
  const quotes=[{key:'clanker_quote',type:'select',options:['native','stock'],asset_kinds:{stock:'stock'}}];
  assert.match(validateLaunchFields(quotes,{clanker_quote:'stock',quote_eligibility:'not_confirmed',dev_buy_sol:0}),/eligibility/);
  assert.match(validateLaunchFields(quotes,{clanker_quote:'stock',quote_eligibility:'confirmed',dev_buy_sol:1}),/creator buy/);
  assert.equal(validateLaunchFields(quotes,{clanker_quote:'stock',quote_eligibility:'confirmed',dev_buy_sol:0}),null);
});
