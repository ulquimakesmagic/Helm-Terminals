import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// Compile src/nav.ts on the fly so the redirect rule is tested exactly as shipped.
const source = await readFile(new URL("../src/nav.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { safeNext } = await import("data:text/javascript," + encodeURIComponent(js));

test("sign-in return paths stay on this site", () => {
  assert.equal(safeNext("/wallets?tab=x"), "/wallets?tab=x");
  for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/login?next=/x"])
    assert.equal(safeNext(bad), "/", String(bad));
});
