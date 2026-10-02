import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// The shared address validators, exercised with Node's test runner; no browser or extra test dependency.
const source = await readFile(new URL('../src/validate.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
const { isSolanaAddr, isEvmAddr, isAddress, TRON_ADDR, NEAR_ACCOUNT, TON_ADDR } =
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

test('extra-chain shapes keep their documented forms', () => {
  assert.equal(TRON_ADDR.test('TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm'), true);
  assert.equal(TRON_ADDR.test('QQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm'), false);
  assert.equal(NEAR_ACCOUNT.test('alice.near'), true);
  assert.equal(NEAR_ACCOUNT.test(`${'ab'.repeat(32)}`), true);
  assert.equal(NEAR_ACCOUNT.test('Alice.near'), false); // NEAR accounts are lowercase
  assert.equal(TON_ADDR.test('EQCDNYVzLkGAjicWD3vEJWFj7W3yZQFYpMGAqBnHgEP46iLW'), true);
  assert.equal(TON_ADDR.test('0:8155303d7ac0e96a98149b24bc46fe13e83dc84558c9407b6af9ee0a3839c6df'), true);
  assert.equal(TON_ADDR.test('EQCDNYVzLkGAjicWD3vEJWFj7W3yZQFYpMGAqBnHgEP46iL'), false); // 47 chars: one short
});
