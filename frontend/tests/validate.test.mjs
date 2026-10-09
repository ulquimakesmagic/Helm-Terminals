import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// The shared address validators, exercised with Node's test runner; no browser or extra test dependency.
const source = await readFile(new URL('../src/validate.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
const { isSolanaAddr, isEvmAddr, isAddress } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('solana and evm shapes match real addresses and reject lookalikes', () => {
  assert.equal(isSolanaAddr('So11111111111111111111111111111111111111112'), true);
  assert.equal(isSolanaAddr('short'), false);
  assert.equal(isSolanaAddr(`0x${'ab'.repeat(20)}`), false);
  assert.equal(isSolanaAddr('0OIl5'), false); // ambiguous base58 characters are not allowed
  assert.equal(isEvmAddr(`0x${'aB'.repeat(20)}`), true);
  assert.equal(isEvmAddr(`0x${'aB'.repeat(19)}`), false);
  assert.equal(isEvmAddr('0x' + 'g'.repeat(40)), false); // g is not a hex character
  assert.equal(isAddress(`0x${'ab'.repeat(20)}`), true);
  assert.equal(isAddress(42), false);
  assert.equal(isAddress(null), false);
});
