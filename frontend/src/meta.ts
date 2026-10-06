/** Per-page document metadata for a client-rendered app. index.html carries the defaults for crawlers that do
 * not run scripts; this keeps titles, descriptions, canonical URLs and indexing rules right after navigation. */
export const SITE = "https://helmterminals.xyz";
export const DEFAULT_DESCRIPTION = "Helm Terminals is a multichain launch desk: create tokens on supported launchpads, manage the wallets behind them, and review every amount and fee before anything is signed.";

function tag(selector: string, create: () => HTMLElement) {
  return (document.head.querySelector(selector) as HTMLElement | null) ?? document.head.appendChild(create());
}

function metaTag(attr: "name" | "property", key: string, content: string) {
  const el = tag(`meta[${attr}="${key}"]`, () => { const m = document.createElement("meta"); m.setAttribute(attr, key); return m; });
  el.setAttribute("content", content);
}

export function setMeta({ title, description = DEFAULT_DESCRIPTION, path, index = true }: { title: string; description?: string; path?: string; index?: boolean }) {
  document.title = title;
  metaTag("name", "description", description);
  metaTag("property", "og:title", title);
  metaTag("property", "og:description", description);
  metaTag("name", "twitter:title", title);
  metaTag("name", "twitter:description", description);
  // Private and account pages are never indexed; robots.txt is not an access control, so they also say so here.
  metaTag("name", "robots", index ? "index, follow" : "noindex, nofollow");
  const canonical = tag('link[rel="canonical"]', () => { const l = document.createElement("link"); l.rel = "canonical"; return l; });
  if (path && index) {
    canonical.setAttribute("href", SITE + path);
    metaTag("property", "og:url", SITE + path);
  } else canonical.remove();
}
