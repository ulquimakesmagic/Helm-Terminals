import fs from 'node:fs';
import solc from 'solc';
function artifact(url, value) {
  const contents = JSON.stringify(value, null, 2) + '\n';
  if (process.argv.includes('--check')) {
    if (!fs.existsSync(url) || fs.readFileSync(url, 'utf8') !== contents) {
      throw new Error(`Artifact differs from source: ${url.pathname}; run npm run build and review the changes`);
    }
  } else fs.writeFileSync(url, contents);
}
const source = fs.readFileSync(new URL('./HelmPonsRewards.sol', import.meta.url), 'utf8');
const input = { language: 'Solidity', sources: { 'HelmPonsRewards.sol': { content: source },
  'RewardTestHelpers.sol': { content: fs.readFileSync(new URL('./RewardTestHelpers.sol', import.meta.url), 'utf8') }
}, settings: {
  optimizer: { enabled: true, runs: 200 }, evmVersion: 'paris',
  outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } }
} };
const output = JSON.parse(solc.compile(JSON.stringify(input)));
for (const error of output.errors ?? []) console.error(error.formattedMessage);
if (output.errors?.some(e => e.severity === 'error')) process.exit(1);
for (const name of ['HelmPonsRewards', 'HelmPonsRewardsFactory']) {
  const c = output.contracts['HelmPonsRewards.sol'][name];
  artifact(new URL(`../backend/app/chains/contracts/${name}.json`, import.meta.url), {
    compiler: solc.version(), settings: input.settings, abi: c.abi,
    bytecode: c.evm.bytecode.object, deployedBytecode: c.evm.deployedBytecode.object
  });
}
const helpers = output.contracts['RewardTestHelpers.sol'];
fs.mkdirSync(new URL('../backend/tests/fixtures/', import.meta.url), { recursive: true });
artifact(new URL('../backend/tests/fixtures/pons-reward-helpers.json', import.meta.url),
  Object.fromEntries(Object.entries(helpers).map(([name, c]) => [name, {
    bytecode: c.evm.bytecode.object, deployedBytecode: c.evm.deployedBytecode.object
  }])));
