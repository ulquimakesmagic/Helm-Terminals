import { createServer } from "node:http";
import { readFile, realpath, stat } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { securityHeaders } from "./security-headers.mjs";

const types = {".html":"text/html; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".css":"text/css; charset=utf-8",
  ".json":"application/json", ".svg":"image/svg+xml", ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
  ".webp":"image/webp", ".ico":"image/x-icon", ".woff":"font/woff", ".woff2":"font/woff2", ".txt":"text/plain"};

export function staticServer(root = fileURLToPath(new URL("./dist", import.meta.url))) {
  const directory = resolve(root);
  return createServer(async (req, res) => {
    Object.entries(securityHeaders).forEach(([key, value]) => res.setHeader(key, value));
    if (!["GET", "HEAD"].includes(req.method)) { res.writeHead(405, {Allow:"GET, HEAD"}); res.end(); return; }
    try {
      const path = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
      if (path.includes("\\") || path.includes("\0") || path.split("/").some(p => p.startsWith("."))) {
        res.writeHead(400); res.end(); return;
      }
      let file = resolve(directory, `.${path}`);
      if (file !== directory && !file.startsWith(directory + sep)) { res.writeHead(403); res.end(); return; }
      let info;
      try { info = await stat(file); } catch {}
      if (!info?.isFile()) {
        if (extname(path) || !(req.headers.accept ?? "").includes("text/html")) { res.writeHead(404); res.end(); return; }
        file = resolve(directory, "index.html");
      }
      const actual = await realpath(file);
      if (!actual.startsWith(directory + sep)) { res.writeHead(403); res.end(); return; }
      const body = await readFile(actual);
      res.setHeader("Content-Type", types[extname(file)] ?? "application/octet-stream");
      res.setHeader("Cache-Control", path.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache");
      res.setHeader("Content-Length", body.length);
      res.writeHead(200); res.end(req.method === "HEAD" ? undefined : body);
    } catch { res.writeHead(400); res.end(); }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  staticServer().listen(Number(process.env.PORT || 4173), "0.0.0.0");
}
