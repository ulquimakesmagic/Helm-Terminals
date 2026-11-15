import { createServer } from "node:http";
import { readFile, realpath, stat } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { request as httpsRequest } from "node:https";
import { request as httpRequest } from "node:http";
import { securityHeaders } from "./security-headers.mjs";

const types = {".html":"text/html; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".css":"text/css; charset=utf-8",
  ".json":"application/json", ".svg":"image/svg+xml", ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
  ".webp":"image/webp", ".ico":"image/x-icon", ".xml":"application/xml; charset=utf-8",
  ".webmanifest":"application/manifest+json", ".woff":"font/woff", ".woff2":"font/woff2", ".txt":"text/plain"};

// Client routes the app renders. Anything else still gets the app shell (so it can show its not-found page and
// navigation), but with HTTP 404 so crawlers and monitors see a real miss instead of a soft 200.
const APP_ROUTES = [/^\/$/, /^\/(login|recover|terms|privacy|risk|launches|wallets|networks|tracker|activity|profile|settings|admin|automation)\/?$/,
  /^\/launches\/[A-Za-z0-9_-]{1,64}\/?$/, /^\/automation\/.*$/, /^\/u\/[A-Za-z0-9_.-]{1,40}\/?$/];
export const isAppRoute = (path) => APP_ROUTES.some((r) => r.test(path));

// Same-origin API proxy. When API_UPSTREAM is set (for example https://api.helmterminals.xyz or Railway's private
// address), browsers call /api/* on this site and the server forwards it. Visitors whose DNS or network can't reach
// the separate api. host still work, and no cross-site (CORS) setup is needed. Only /api/* is forwarded, only to the
// one configured upstream, with no cookies and a capped body and time.
const API_PREFIX = "/api/";
const MAX_API_BODY = 8 * 1024 * 1024;
const API_TIMEOUT_MS = 60_000;
const HOP_BY_HOP = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
  "transfer-encoding", "upgrade", "host", "content-length", "cookie", "set-cookie", "forwarded", "x-forwarded-for",
  "x-forwarded-host", "x-forwarded-proto", "x-real-ip"]);

export function parseUpstream(value) {
  if (!value) return null;
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("API_UPSTREAM must be a plain http(s) URL");
  return url;
}

function proxyApi(req, res, upstream) {
  const path = req.url.slice(API_PREFIX.length - 1); // keeps the leading slash: "/api/chains?x=1" -> "/chains?x=1"
  let target;
  try { target = new URL(upstream.origin + path); } catch { res.writeHead(400); res.end(); return; }
  if (target.origin !== upstream.origin || path.includes("\\") || path.includes("\0")) { res.writeHead(400); res.end(); return; }
  const declared = Number(req.headers["content-length"] ?? 0);
  if (declared > MAX_API_BODY) { res.writeHead(413); res.end(); return; }
  const headers = {};
  for (const [k, v] of Object.entries(req.headers)) if (!HOP_BY_HOP.has(k)) headers[k] = v;
  headers.host = upstream.host;
  // Append the peer we saw to the chain; the API only reads it from peers listed in TRUSTED_PROXY_CIDRS.
  const peer = req.socket.remoteAddress ?? "";
  const chain = String(req.headers["x-forwarded-for"] ?? "").trim();
  headers["x-forwarded-for"] = chain ? `${chain}, ${peer}` : peer;
  headers["x-forwarded-proto"] = "https";
  const send = upstream.protocol === "https:" ? httpsRequest : httpRequest;
  let received = 0;
  const out = send(target, { method: req.method, headers, timeout: API_TIMEOUT_MS }, (back) => {
    const reply = {};
    for (const [k, v] of Object.entries(back.headers)) if (!HOP_BY_HOP.has(k) && !k.startsWith("access-control-")) reply[k] = v;
    res.writeHead(back.statusCode ?? 502, reply);
    back.pipe(res);
  });
  out.on("timeout", () => out.destroy(new Error("upstream timeout")));
  out.on("error", () => { if (!res.headersSent) { res.writeHead(502, {"Content-Type": "application/json"}); res.end('{"detail":"The Helm Terminals API did not answer."}'); } else res.destroy(); });
  req.on("data", (chunk) => { received += chunk.length; if (received > MAX_API_BODY) { out.destroy(); if (!res.headersSent) { res.writeHead(413); res.end(); } req.destroy(); } });
  req.on("aborted", () => out.destroy());
  req.pipe(out);
}

export function staticServer(root = fileURLToPath(new URL("./dist", import.meta.url)), apiUpstream = parseUpstream(process.env.API_UPSTREAM)) {
  const directory = resolve(root);
  return createServer(async (req, res) => {
    Object.entries(securityHeaders).forEach(([key, value]) => res.setHeader(key, value));
    if (apiUpstream && req.url.startsWith(API_PREFIX)) { proxyApi(req, res, apiUpstream); return; }
    if (!["GET", "HEAD"].includes(req.method)) { res.writeHead(405, {Allow:"GET, HEAD"}); res.end(); return; }
    try {
      const path = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
      if (path.includes("\\") || path.includes("\0") || path.split("/").some(p => p.startsWith("."))) {
        res.writeHead(400); res.end(); return;
      }
      let file = resolve(directory, `.${path}`);
      if (file !== directory && !file.startsWith(directory + sep)) { res.writeHead(403); res.end(); return; }
      let info;
      let status = 200;
      try { info = await stat(file); } catch {}
      if (!info?.isFile()) {
        if (extname(path) || !(req.headers.accept ?? "").includes("text/html")) { res.writeHead(404); res.end(); return; }
        file = resolve(directory, "index.html");
        if (!isAppRoute(path)) status = 404;
      }
      const actual = await realpath(file);
      if (!actual.startsWith(directory + sep)) { res.writeHead(403); res.end(); return; }
      const body = await readFile(actual);
      res.setHeader("Content-Type", types[extname(file)] ?? "application/octet-stream");
      res.setHeader("Cache-Control", path.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache");
      res.setHeader("Content-Length", body.length);
      res.writeHead(status); res.end(req.method === "HEAD" ? undefined : body);
    } catch { res.writeHead(400); res.end(); }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  staticServer().listen(Number(process.env.PORT || 4173), "0.0.0.0");
}
