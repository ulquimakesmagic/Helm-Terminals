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
// The reviewed artifacts were compiled from CRLF sources. Normalize compiler input on
// every OS to preserve their exact metadata hash, runtime bytes and CREATE2 addresses.
// Do not normalize generated artifacts or weaken the byte-for-byte drift comparison.
const source = (name) => fs.readFileSync(new URL(`./${name}`, import.meta.url), 'utf8')
  .replace(/\r\n?/g, '\n').replace(/\n/g, '\r\n');
const settings = {
  optimizer: { enabled: true, runs: 200 }, evmVersion: 'paris',
  outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } }
};
const CONTRACTS = {
  'HelmPonsRewards.sol': ['HelmPonsRewards', 'HelmPonsRewardsFactory'],
  'HelmRewardsSplitter.sol': ['HelmRewardsSplitter', 'HelmRewardsSplitterFactory'],
};

// Each launchpad contract compiles in its own solc invocation: solc's metadata hash covers every
// source in the input, so a shared compilation would change one contract's bytes whenever another
// file is added — invalidating a reviewed artifact or an explorer verification for no reason.
function compile(sources) {
  const output = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity', sources, settings })));
  for (const error of output.errors ?? []) console.error(error.formattedMessage);
  if (output.errors?.some(e => e.severity === 'error')) process.exit(1);
  return output.contracts;
}

for (const [file, names] of Object.entries(CONTRACTS)) {
  const contracts = compile({ [file]: { content: source(file) },
    'RewardTestHelpers.sol': { content: source('RewardTestHelpers.sol') } });
  for (const name of names) {
    const c = contracts[file][name];
    artifact(new URL(`../backend/app/chains/contracts/${name}.json`, import.meta.url), {
      compiler: solc.version(), settings, abi: c.abi,
      bytecode: c.evm.bytecode.object, deployedBytecode: c.evm.deployedBytecode.object
    });
  }
}
fs.mkdirSync(new URL('../backend/tests/fixtures/', import.meta.url), { recursive: true });
artifact(new URL('../backend/tests/fixtures/reward-helpers.json', import.meta.url),
  Object.fromEntries(Object.entries(compile({ 'RewardTestHelpers.sol': { content: source('RewardTestHelpers.sol') } })['RewardTestHelpers.sol'])
    .map(([name, c]) => [name, {
      bytecode: c.evm.bytecode.object, deployedBytecode: c.evm.deployedBytecode.object
    }])));
