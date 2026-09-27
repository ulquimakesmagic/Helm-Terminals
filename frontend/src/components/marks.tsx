import { chainOf, padName } from "../chains";

/*
 * Brand marks for chains and launchpads. These are identity marks, kept apart from the functional icon set in
 * ui.tsx. The repo ships no official logo files, so each mark is a monogram tinted with its chain's colour.
 * A mark is always paired with a visible name (or given one via `label`); colour never carries meaning alone.
 */

const CHAIN_MONO: Record<string, string> = { solana: "S", base: "B", bsc: "BN", robinhood: "RH", ethereum: "E" };

const PAD_MONO: Record<string, string> = {
  pumpfun: "PF", letsbonk: "LB", "meteora-dbc": "MD", bags: "BG", printr: "PR", surge: "SG", anoncoin: "AN",
  stonkfun: "SF", clanker: "CL", "clanker-robinhood": "CL", bankr: "BK", "four-meme": "4M", "flap-sh": "FL",
  "flap-sh-robinhood": "FL", pons: "PO", "uniswap-v2": "U2",
};

function monoFont(text: string, size: number) {
  return Math.round(size * (text.length >= 2 ? 0.36 : 0.46));
}

/** A chain's mark. Decorative unless `label` is set (use it when no chain name is printed next to it). */
export function ChainMark({ network, size = 18, label }: { network?: string | null; size?: number; label?: boolean }) {
  const c = chainOf(network);
  const text = CHAIN_MONO[c.id] ?? c.name.slice(0, 1);
  return (
    <span className="mark chain-mark" title={c.name} role={label ? "img" : undefined} aria-label={label ? c.name : undefined}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size, fontSize: monoFont(text, size), color: c.color, background: `${c.color}1f`, borderColor: `${c.color}59`, borderRadius: Math.max(4, size * 0.3) }}>
      {text}
    </span>
  );
}

/** A launchpad's mark, with a small chain badge in the corner so the same pad on two chains stays distinct. */
export function PadMark({ pad, network, size = 22, label }: { pad: string; network?: string | null; size?: number; label?: boolean }) {
  const c = chainOf(network);
  const text = PAD_MONO[pad] ?? padName(pad).replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase();
  const name = `${padName(pad)} on ${c.name}`;
  return (
    <span className="mark pad-mark" title={name} role={label ? "img" : undefined} aria-label={label ? name : undefined}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size, fontSize: monoFont(text, size), borderRadius: Math.max(5, size * 0.28) }}>
      {text}
      <i style={{ background: c.color, width: Math.max(6, size * 0.3), height: Math.max(6, size * 0.3) }} />
    </span>
  );
}

/** Mark + name, the standard way to show a launchpad in lists and tables. */
export function PadLabel({ pad, network, size = 20, showChain }: { pad: string; network?: string | null; size?: number; showChain?: boolean }) {
  return (
    <span className="pad-label">
      <PadMark pad={pad} network={network} size={size} />
      <span className="truncate">{padName(pad)}{showChain && <span className="muted"> · {chainOf(network).name}</span>}</span>
    </span>
  );
}

/** Mark + name for a chain. */
export function ChainLabel({ network, size = 18, hideNameSm }: { network?: string | null; size?: number; hideNameSm?: boolean }) {
  const c = chainOf(network);
  return (
    <span className="pad-label">
      <ChainMark network={network} size={size} label={hideNameSm} />
      <span className={hideNameSm ? "hide-sm" : undefined}>{c.name}</span>
    </span>
  );
}
