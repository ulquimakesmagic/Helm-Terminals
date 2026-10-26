import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { staticServer, parseUpstream } from "../server.mjs";

const listen = (server) => new Promise((r) => server.listen(0, "127.0.0.1", r));
const close = (server) => new Promise((r) => { server.closeAllConnections?.(); server.close(r); });

async function withProxy(handler, run) {
  const seen = [];
  const upstream = createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => { seen.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString() }); handler(req, res); });
  });
  await listen(upstream);
  const root = await mkdtemp(join(tmpdir(), "helm-proxy-"));
  await writeFile(join(root, "index.html"), "<!doctype html><title>Fixture</title>");
  const site = staticServer(root, parseUpstream(`http://127.0.0.1:${upstream.address().port}`));
  await listen(site);
  try { await run(`http://127.0.0.1:${site.address().port}`, seen); }
  finally { await close(site); await close(upstream); await rm(root, { recursive: true, force: true }); }
}

test("/api/* is forwarded to the upstream with method, query, body and bearer token", async () => {
  await withProxy((req, res) => { res.writeHead(201, { "Content-Type": "application/json", "Set-Cookie": "a=b", "Access-Control-Allow-Origin": "*" }); res.end('{"ok":true}'); },
    async (url, seen) => {
      const r = await fetch(`${url}/api/chains?x=1`, { method: "POST", headers: { Authorization: "Bearer t0k", "Content-Type": "application/json", Cookie: "s=1", "X-Forwarded-For": "9.9.9.9" }, body: '{"a":1}' });
      assert.equal(r.status, 201);
      assert.deepEqual(await r.json(), { ok: true });
      assert.equal(r.headers.get("set-cookie"), null);
      assert.equal(r.headers.get("access-control-allow-origin"), null);
      assert.match(r.headers.get("content-security-policy"), /script-src 'self'/);
      const [call] = seen;
      assert.equal(call.method, "POST");
      assert.equal(call.url, "/chains?x=1");
      assert.equal(call.body, '{"a":1}');
      assert.equal(call.headers.authorization, "Bearer t0k");
      assert.equal(call.headers.cookie, undefined);
      assert.equal(call.headers["x-forwarded-for"], "9.9.9.9, 127.0.0.1");
    });
});

test("only /api/ is proxied: other paths are still the static site, and tricks stay on the upstream origin", async () => {
  await withProxy((req, res) => { res.end("{}"); }, async (url, seen) => {
    assert.equal((await fetch(`${url}/`, { headers: { Accept: "text/html" } })).status, 200);
    assert.equal((await fetch(`${url}/apiary`, { method: "POST" })).status, 405);
    await fetch(`${url}/api//evil.example/x`);  // a protocol-relative lookalike stays a path on the upstream
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, "//evil.example/x");
    assert.match(seen[0].headers.host, /^127\.0\.0\.1:\d+$/);
  });
});

test("an unreachable upstream gives a JSON 502, and an oversized body is refused", async () => {
  const root = await mkdtemp(join(tmpdir(), "helm-proxy-502-"));
  await writeFile(join(root, "index.html"), "x");
  const site = staticServer(root, parseUpstream("http://127.0.0.1:1"));
  await listen(site);
  try {
    const url = `http://127.0.0.1:${site.address().port}`;
    const r = await fetch(`${url}/api/health/ready`);
    assert.equal(r.status, 502);
    assert.match((await r.json()).detail, /did not answer/);
    const big = await fetch(`${url}/api/x`, { method: "POST", body: "a".repeat(9 * 1024 * 1024) });
    assert.equal(big.status, 413);
  } finally { await close(site); await rm(root, { recursive: true, force: true }); }
});

test("without API_UPSTREAM nothing is proxied, and bad upstream values are rejected", async () => {
  const root = await mkdtemp(join(tmpdir(), "helm-proxy-off-"));
  await writeFile(join(root, "index.html"), "x");
  const site = staticServer(root, null);
  await listen(site);
  try { assert.equal((await fetch(`http://127.0.0.1:${site.address().port}/api/chains`, { method: "POST" })).status, 405); }
  finally { await close(site); await rm(root, { recursive: true, force: true }); }
  assert.equal(parseUpstream(""), null);
  assert.throws(() => parseUpstream("ftp://x"));
  assert.throws(() => parseUpstream("https://user:pw@x.example"));
});
