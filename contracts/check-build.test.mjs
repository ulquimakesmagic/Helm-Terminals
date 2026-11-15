import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
test('LF and CRLF compile to pinned bytes; actual source drift fails without rewriting artifacts', async () => {
  // A disposable tree only; never change source/artifacts in the working checkout.
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'helm-artifact-check-'));
  try {
    const contracts = path.join(scratch, 'contracts');
    const artifacts = path.join(scratch, 'backend/app/chains/contracts');
    const fixtures = path.join(scratch, 'backend/tests/fixtures');
    await fs.mkdir(contracts, {recursive:true});
    await fs.mkdir(artifacts, {recursive:true});
    await fs.mkdir(fixtures, {recursive:true});
    // Resolve installed compiler without fetching dependencies or executing package scripts.
    await fs.symlink(path.join(root, 'contracts/node_modules'), path.join(contracts, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
    await fs.copyFile(path.join(root, 'contracts/build.mjs'), path.join(contracts, 'build.mjs'));
    for (const name of ['HelmPonsRewards','HelmPonsRewardsFactory','HelmPonsRewardsV2','HelmPonsRewardsV2Factory','HelmRewardsSplitter','HelmRewardsSplitterFactory'])
      await fs.copyFile(path.join(root, `backend/app/chains/contracts/${name}.json`), path.join(artifacts, `${name}.json`));
    await fs.copyFile(path.join(root, 'backend/tests/fixtures/reward-helpers.json'), path.join(fixtures, 'reward-helpers.json'));
    const sources = ['HelmPonsRewards.sol','HelmPonsRewardsV2.sol','HelmRewardsSplitter.sol','RewardTestHelpers.sol'];
    const check = () => spawnSync(process.execPath, ['build.mjs','--check'], {cwd:contracts, encoding:'utf8', timeout:60000, windowsHide:true});
    for (const ending of ['\n','\r\n']) {
      for (const name of sources) await fs.writeFile(path.join(contracts,name), (await fs.readFile(path.join(root,'contracts',name),'utf8')).replace(/\r\n?/g,'\n').replace(/\n/g,ending));
      const result = check();
      assert.equal(result.status, 0, result.stderr);
    }
    const saved = await fs.readFile(path.join(artifacts, 'HelmPonsRewards.json'), 'utf8');
    await fs.appendFile(path.join(contracts, 'HelmPonsRewards.sol'), '\n// controlled source drift\n');
    const result = check();
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Artifact differs from source/);
    assert.equal(await fs.readFile(path.join(artifacts,'HelmPonsRewards.json'),'utf8'), saved);
  } finally {
    // Remove the junction first, then only this function's absolute temporary directory.
    assert.ok(path.resolve(scratch).startsWith(path.resolve(os.tmpdir()) + path.sep));
    assert.ok(path.basename(scratch).startsWith('helm-artifact-check-'));
    const junction = path.join(scratch, 'contracts/node_modules');
    const stat = await fs.lstat(junction).catch(() => null);
    if (stat) { assert.ok(stat.isSymbolicLink()); await fs.unlink(junction); }
    await fs.rm(scratch, {recursive:true, force:true});
  }
});
