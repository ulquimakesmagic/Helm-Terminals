import { Pad, padName, padsByChain } from "../chains";
import { ChainMark, PadMark } from "./marks";

/** Every launchpad, grouped by chain, with live / coming-soon status. */
export function LaunchpadDirectory({ pads, onPick, picked }: { pads: Pad[]; onPick?: (p: Pad) => void; picked?: string }) {
  return (
    <div>
      {padsByChain(pads).map(({ chain, pads: list }) => (
        <div key={chain.id} className="chain-group">
          <div className="chain-group-head">
            <ChainMark network={chain.id} size={18} />
            {chain.name}
            <span className="muted small" style={{ fontWeight: 400 }}>
              {list.filter((p) => p.status === "available").length} live · {list.length} total
            </span>
          </div>
          <div className="pad-grid">
            {list.map((p) => {
              const live = p.status === "available";
              const setup = p.status === "setup_required";
              return (
                <button key={p.id} type="button" className={`pad-tile ${picked === p.id ? "on" : ""}`}
                  disabled={!live || !onPick} style={!onPick && live ? { cursor: "default" } : undefined} aria-pressed={onPick ? picked === p.id : undefined}
                  title={p.reason ?? (live ? undefined : "Coming soon")} onClick={() => onPick?.(p)}>
                  <PadMark pad={p.id} network={p.network} size={24} />
                  <span className="grow" style={{ fontWeight: 600, overflowWrap: "anywhere", lineHeight: 1.25 }}>{padName(p.id)}</span>
                  <span className={`badge ${live ? "good" : setup ? "warn" : ""}`}><span className="dot" />{live ? "Live" : setup ? "Needs setup" : "Soon"}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export function coverage(pads: Pad[]) {
  const chains = new Set(pads.map((p) => p.network));
  return { chains: chains.size, pads: pads.length, live: pads.filter((p) => p.status === "available").length };
}
