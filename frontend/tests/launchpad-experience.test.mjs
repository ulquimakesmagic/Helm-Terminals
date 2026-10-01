import assert from "node:assert/strict";
import test from "node:test";

import { LAUNCHPAD_EXPERIENCES, launchpadExperience } from "../src/launchpadExperience.ts";

const IMPLEMENTED = [
  "pumpfun", "letsbonk", "bags", "flap-sh", "flap-sh-robinhood", "pons", "four-meme",
  "uniswap-v2", "meteora-dbc", "clanker", "clanker-robinhood", "sunpump", "meme-cooking", "tolly",
];

test("every implemented launchpad has a specific launch experience", () => {
  assert.deepEqual(Object.keys(LAUNCHPAD_EXPERIENCES).sort(), IMPLEMENTED.sort());
  for (const id of IMPLEMENTED) {
    const experience = launchpadExperience(id);
    assert.notEqual(experience.title, "Configure this launch", id);
    assert.ok(experience.features.length >= 3);
  }
});

test("claim controls reflect the adapters Helm actually supports", () => {
  for (const id of ["pumpfun", "bags", "meteora-dbc", "clanker", "clanker-robinhood", "pons"])
    assert.equal(launchpadExperience(id).claimMode, "in-app", id);
  assert.equal(launchpadExperience("letsbonk").claimMode, "external");
  assert.equal(launchpadExperience("flap-sh").claimMode, "automatic");
  assert.equal(launchpadExperience("meme-cooking").claimMode, "auction");
  for (const id of ["four-meme", "uniswap-v2", "sunpump", "tolly"])
    assert.equal(launchpadExperience(id).claimMode, "none", id);
});
