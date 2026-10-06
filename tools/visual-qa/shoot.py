"""Offline screenshots and layout checks of the built web app against captured API fixtures.

    cd frontend && VITE_API_URL=https://api.local npm run build && PORT=4173 node server.mjs &
    python ../tools/visual-qa/shoot.py ../tools/visual-qa/fixtures.json <out-dir> [label]

Signs in through the real login form (the token stays in memory, as in production), then moves between pages with
client-side navigation. For each page and viewport it saves a screenshot and records horizontal overflow, elements
wider than the viewport, small touch targets, images without alt text and unlabeled form controls.
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

APP = "http://localhost:4173"
VIEWPORTS = {"320": (320, 720), "390": (390, 844), "768": (768, 1024), "1440": (1440, 900), "1920": (1920, 1080)}

AUDIT_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const out = {overflow: document.documentElement.scrollWidth > vw + 1, wide: [], small_targets: [], no_alt: [], unlabeled: []};
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && !el.closest('.table-wrap, [data-scroll-x], .ws-tablist, .seg, .modal-body') && getComputedStyle(el).position !== 'fixed')
      out.wide.push((el.className && String(el.className).slice(0, 40)) || el.tagName);
  }
  for (const el of document.querySelectorAll('a[href], button, input, select, textarea, [role=button]')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if ((r.width < 24 || r.height < 24) && !el.closest('p, li, td, dd'))
      out.small_targets.push((el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 30));
  }
  for (const img of document.querySelectorAll('img')) if (!img.hasAttribute('alt')) out.no_alt.push(img.src.slice(-40));
  for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    if (!visible(el)) continue;
    const labelled = el.labels?.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.closest('label');
    if (!labelled) out.unlabeled.push(el.name || el.placeholder || el.type);
  }
  out.wide = [...new Set(out.wide)].slice(0, 8); out.small_targets = [...new Set(out.small_targets)].slice(0, 12);
  return out;
}"""


def main(fixtures_path, out_dir, label="run", pages=None):
    fixtures = json.load(open(fixtures_path))
    lid = fixtures["_launch_id"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    public = [("landing", "/"), ("login", "/login"), ("register", "/login?mode=register"), ("recover", "/recover"),
              ("terms", "/terms"), ("privacy", "/privacy"), ("risk", "/risk"), ("not-found", "/no-such-page")]
    private = [("dashboard", "/"), ("launches", "/launches"), ("workspace", f"/launches/{lid}"), ("wallets", "/wallets"),
               ("networks", "/networks"), ("tracker", "/tracker"), ("activity", "/activity"), ("profile", "/profile"),
               ("settings", "/settings"), ("admin-denied", "/admin"), ("private-not-found", "/no-such-page")]
    if pages:
        public = [p for p in public if p[0] in pages]
        private = [p for p in private if p[0] in pages]
    report = {}

    def route(r):
        url = urlsplit(r.request.url)
        key = url.path + (f"?{url.query}" if url.query else "")
        if r.request.method == "POST" and url.path == "/auth/login":
            return r.fulfill(status=200, json={"token": "visual-qa", "expires_at": "2099-01-01T00:00:00"})
        if r.request.method == "POST":
            return r.fulfill(status=400, json={"detail": "Visual QA is read-only"})
        hit = fixtures.get(key) or fixtures.get(url.path)
        if hit is None and url.path.startswith("/admin"):
            return r.fulfill(status=403, json={"detail": "not allowed"})
        if hit is None:
            return r.fulfill(status=404, json={"detail": "not found"})
        body = hit["body"]
        if isinstance(body, dict) and "fetched_at" in body:
            body = {**body, "fetched_at": time.time()}  # captured earlier; present it as fresh
        return r.fulfill(status=hit["status"], json=body)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp, (w, h) in VIEWPORTS.items():
            ctx = browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce")
            page = ctx.new_page()
            page.route("https://api.local/**", route)
            for name, path in public:
                page.goto(APP + path)
                page.wait_for_timeout(700)
                page.screenshot(path=str(out / f"{label}-{name}-{vp}.png"), full_page=vp in ("390", "1440"))
                report[f"{name}@{vp}"] = page.evaluate(AUDIT_JS)
            page.goto(APP + "/login")
            page.wait_for_timeout(400)
            page.fill("input[type=email]", "operator@example.com")
            page.fill("input[type=password]", "visual-qa-password")
            page.keyboard.press("Enter")
            page.wait_for_timeout(900)
            for name, path in private:
                page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }", path)
                page.wait_for_timeout(1200)
                page.screenshot(path=str(out / f"{label}-{name}-{vp}.png"), full_page=vp in ("390", "1440"))
                report[f"{name}@{vp}"] = page.evaluate(AUDIT_JS)
            ctx.close()
        browser.close()
    (out / f"{label}-layout-report.json").write_text(json.dumps(report, indent=1))
    problems = {k: v for k, v in report.items() if v["overflow"] or v["wide"] or v["no_alt"] or v["unlabeled"]}
    print(json.dumps({"pages": len(report), "with_problems": len(problems)}, indent=1))
    for k, v in problems.items():
        print(k, {kk: vv for kk, vv in v.items() if vv and kk != "small_targets"})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "run",
         set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None)
