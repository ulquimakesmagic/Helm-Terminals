import { useState } from "react";
import { chainOf, isExtraChain, padName } from "../chains";

/*
 * Brand marks for chains and launchpads: the projects' own logo files, stored locally under
 * src/assets/brands/ (sources in src/assets/brands/SOURCES.md). These are identity marks, kept apart from the
 * functional icon set in ui.tsx, and always shown beside a visible name, never in place of one.
 *
 * Files are found by name, so adding an official file such as launchpads/clanker.png makes it appear with no
 * code change. When no verified file exists, the slot stays an empty, neutral tile: no initials, no stand-in.
 */

const files = import.meta.glob("../assets/brands/*/*.{svg,png,jpg,jpeg,webp}", { eager: true, query: "?url", import: "default" }) as Record<string, string>;

function asset(kind: "chains" | "launchpads", key: string): string | undefined {
  for (const ext of ["svg", "png", "webp", "jpg", "jpeg"]) {
    const url = files[`../assets/brands/${kind}/${key}.${ext}`];
    if (url) return url;
  }
  return undefined;
}

/** Launchpad ids that share one brand. The same launchpad on another chain keeps its own logo. */
const PAD_BRAND: Record<string, string> = {
  "meteora-dbc": "meteora", "uniswap-v2": "uniswap", "flap-sh": "flap", "flap-sh-robinhood": "flap",
  "clanker-robinhood": "clanker",
};

/*
 * How each file sits in the tile, without altering it: "fill" logos already carry their own background shape (a
 * circle or rounded square) and fill the tile; "pad" logos are bare marks drawn on the neutral tile with a little
 * inset. `bg` is a brand-approved backdrop: Robinhood Chain's black feather is shown on Robin Neon (#ccff00), one
 * of the three pairings in its brand guidelines.
 */
const TREATMENT: Record<string, { fit: "fill" | "pad"; bg?: string; round?: boolean }> = {
  "chains/solana": { fit: "pad" }, "chains/base": { fit: "fill", round: true }, "chains/bsc": { fit: "pad" },
  "chains/ethereum": { fit: "pad" }, "chains/robinhood": { fit: "pad", bg: "#ccff00" },
  "launchpads/pumpfun": { fit: "pad" }, "launchpads/four-meme": { fit: "pad" }, "launchpads/meteora": { fit: "pad" },
  "launchpads/flap": { fit: "pad" }, "launchpads/uniswap": { fit: "fill" }, "launchpads/bankr": { fit: "fill" },
  "launchpads/printr": { fit: "fill" }, "launchpads/pons": { fit: "fill" }, "launchpads/surge": { fit: "fill" },
  "launchpads/anoncoin": { fit: "fill" }, "launchpads/bags": { fit: "fill" }, "launchpads/stonkfun": { fit: "fill" },
  "launchpads/clanker": { fit: "fill" }, "launchpads/letsbonk": { fit: "fill", round: true },
};

const reported = new Set<string>();
function reportProblem(what: string, detail: string) {
  if (!import.meta.env.DEV || reported.has(what)) return;
  reported.add(what);
  console.warn(`[brand-mark] ${what}: ${detail}`);
}

/** One logo in a fixed square tile. Decorative by default; `label` makes it an image with that accessible name. */
function BrandTile({ kind, brand, name, size, label, className, text }: {
  kind: "chains" | "launchpads"; brand: string; name: string; size: number; label?: boolean; className: string; text?: string;
}) {
  const src = asset(kind, brand);
  const [failed, setFailed] = useState(false);
  const t = TREATMENT[`${kind}/${brand}`] ?? { fit: "pad" };
  const radius = t.round ? "50%" : Math.max(4, Math.round(size * 0.28));
  const a11y = label ? { role: "img", "aria-label": name } : { "aria-hidden": true as const };
  if ((!src || failed) && text) {
    // No verified logo file (TRON, NEAR, Arc): a text mark instead of a guessed logo.
    return <span className={`brand ${className} brand-text`} title={name} {...a11y}
      style={{ width: size, height: size, borderRadius: radius, fontSize: Math.max(6, Math.min(size * 0.34, (size - 3) / (text.length * 0.62))) }}>{text}</span>;
  }
  if (!src || failed) {
    if (!src) reportProblem(`${kind}/${brand}`, "no verified logo file; showing an empty tile beside the name");
    // Neutral, empty tile keeps alignment; in development a missing or broken file is outlined so it's noticed.
    return <span className={`brand ${className} brand-missing ${failed && import.meta.env.DEV ? "brand-broken" : ""}`}
      title={failed ? `${name} (logo failed to load)` : name} style={{ width: size, height: size, borderRadius: radius }} {...a11y} />;
  }
  const inset = t.fit === "pad" ? Math.max(2, Math.round(size * 0.14)) : 0;
  return (
    <span className={`brand ${className} brand-${t.fit}`} title={name} {...a11y}
      style={{ width: size, height: size, borderRadius: radius, padding: inset, ...(t.bg ? { background: t.bg, borderColor: "transparent" } : {}) }}>
      <img src={src} alt="" draggable={false} decoding="async"
        onError={() => { reportProblem(`${kind}/${brand}`, `failed to load ${src}`); setFailed(true); }} />
    </span>
  );
}

/** A chain's logo. Decorative unless `label` is set (use it when no chain name is printed next to it). */
export function ChainMark({ network, size = 18, label }: { network?: string | null; size?: number; label?: boolean }) {
  const c = chainOf(network);
  return <BrandTile kind="chains" brand={c.id} name={c.name} size={size} label={label} className="chain-mark"
    text={isExtraChain(c.id) ? c.short : undefined} />;
}

/** A launchpad's own logo. The chain is shown separately (ChainMark / PadLabel `showChain`), never baked in. */
export function PadMark({ pad, size = 22, label }: { pad: string; network?: string | null; size?: number; label?: boolean }) {
  return <BrandTile kind="launchpads" brand={PAD_BRAND[pad] ?? pad} name={padName(pad)} size={size} label={label} className="pad-mark" />;
}

/** Launchpad logo + name, and optionally which chain it's on (its own logo + name), the standard label in lists. */
export function PadLabel({ pad, network, size = 20, showChain }: { pad: string; network?: string | null; size?: number; showChain?: boolean }) {
  const c = chainOf(network);
  return (
    <span className="pad-label">
      <PadMark pad={pad} size={size} />
      <span className="truncate">{padName(pad)}</span>
      {showChain && (
        <span className="pad-chain muted">on <ChainMark network={c.id} size={Math.max(12, size - 6)} />{c.name}</span>
      )}
    </span>
  );
}

/** Chain logo + name. */
export function ChainLabel({ network, size = 18, hideNameSm }: { network?: string | null; size?: number; hideNameSm?: boolean }) {
  const c = chainOf(network);
  return (
    <span className="pad-label">
      <ChainMark network={network} size={size} label={hideNameSm} />
      <span className={hideNameSm ? "hide-sm" : undefined}>{c.name}</span>
    </span>
  );
}
