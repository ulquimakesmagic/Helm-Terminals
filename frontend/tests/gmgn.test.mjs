import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// Exercise the production URL builder with Node's test runner; no browser or extra test dependency.
const source = await readFile(new URL('../src/gmgn.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
const { gmgnChartUrl, CHART_INTERVALS } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const sol = 'So11111111111111111111111111111111111111112';
const evm = `0x${'aB'.repeat(20)}`;

test('documented chain identifiers, token case and interval values reach GMGN unchanged', () => {
  for (const [network, chain, token] of [['solana', 'sol', sol], ['ethereum', 'eth', evm], ['bsc', 'bsc', evm]]) {
    for (const [interval] of CHART_INTERVALS) {
      const url = new URL(gmgnChartUrl(network, token, interval));
      assert.equal(url.origin, 'https://www.gmgn.cc');
      assert.equal(url.pathname, `/kline/${chain}/${token}`);
      assert.deepEqual([...url.searchParams], [['theme', 'dark'], ['interval', interval]]);
    }
  }
});
test('unsupported API chains are never guessed to be supported iframe chains', () => {
  for (const network of ['base', 'robinhood', 'arc', 'tron', 'ton', 'near', 'unknown', 'constructor', '__proto__']) {
    assert.equal(gmgnChartUrl(network, evm, '15'), null);
  }
});
test('cross-chain addresses, injection, missing tokens and undocumented intervals are rejected', () => {
  for (const token of [null, '', 'WSOL', evm, `${sol}?pool=x`, `../${sol}`, `${sol}\n`]) {
    assert.equal(gmgnChartUrl('solana', token, '15'), null);
  }
  for (const token of [sol, '0x123', `${evm}/`, `https://evil.example/${evm}`]) {
    assert.equal(gmgnChartUrl('ethereum', token, '15'), null);
  }
  assert.equal(gmgnChartUrl('solana', sol, '30'), null);
});
