import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { staticServer } from "../server.mjs";

test("production server serves deep links, enforces script CSP and refuses missing assets/mutations", async () => {
  const root = await mkdtemp(join(tmpdir(), "helm-static-test-"));
  await writeFile(join(root, "index.html"), "<!doctype html><title>Fixture</title>");
  const server = staticServer(root);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  try {
    const page = await fetch(`${url}/recover`, {headers: {Accept: "text/html"}});
    assert.equal(page.status, 200);
    assert.match(page.headers.get("content-security-policy"), /script-src 'self';/);
    assert.equal(page.headers.get("cache-control"), "no-cache");
    assert.equal((await fetch(`${url}/missing.js`)).status, 404);
    assert.equal((await fetch(`${url}/.env`)).status, 400);
    assert.equal((await fetch(url, {method:"POST"})).status, 405);
    assert.equal((await fetch(`${url}/%00`, {headers:{Accept:"text/html"}})).status, 400);
  } finally {
    await new Promise(resolve => server.close(resolve));
    await rm(root, {recursive:true, force:true});
  }
});
