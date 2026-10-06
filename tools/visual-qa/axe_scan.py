"""axe-core WCAG 2.2 A/AA scan of each page against captured fixtures (same setup as shoot.py).

    python ../tools/visual-qa/axe_scan.py fixtures.json path/to/axe.min.js
"""
import json
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

APP = "http://localhost:4173"


def main(fixtures_path, axe_path):
    fx = json.load(open(fixtures_path))
    axe = open(axe_path).read()
    lid = fx["_launch_id"]
    public = ["/", "/login", "/recover", "/terms", "/privacy", "/risk", "/nope"]
    private = ["/", "/launches", f"/launches/{lid}", "/wallets", "/networks", "/tracker", "/activity", "/profile", "/settings", "/admin"]

    def route(r):
        u = urlsplit(r.request.url)
        if r.request.method == "POST":
            return r.fulfill(status=200, json={"token": "t", "expires_at": "2099-01-01T00:00:00"})
        h = fx.get(u.path + (f"?{u.query}" if u.query else "")) or fx.get(u.path)
        body = h["body"] if h else {"detail": "not found"}
        if isinstance(body, dict) and "fetched_at" in body:
            body = {**body, "fetched_at": time.time()}
        return r.fulfill(status=h["status"] if h else (403 if u.path.startswith("/admin") else 404), json=body)

    results = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 1280, "height": 900}, bypass_csp=True).new_page()  # only to inject axe
        pg.route("https://api.local/**", route)

        def scan(name):
            pg.wait_for_timeout(1000)
            pg.add_script_tag(content=axe)
            res = pg.evaluate("""async () => { const r = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}});
              return r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0]?.target?.join(' ')})); }""")
            results[name] = res
        for path in public:
            pg.goto(APP + path)
            scan("public " + path)
        pg.goto(APP + "/login")
        pg.wait_for_timeout(400)
        pg.fill("input[type=email]", "a@b.co")
        pg.fill("input[type=password]", "x" * 12)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(800)
        for path in private:
            pg.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }", path)
            scan("app " + path)
        b.close()
    total = sum(v["nodes"] for r in results.values() for v in r)
    print(json.dumps({"pages": len(results), "violation_nodes": total}, indent=1))
    for k, r in results.items():
        for v in r:
            print(k, v)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
