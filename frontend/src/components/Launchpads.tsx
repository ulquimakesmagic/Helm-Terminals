import { Pad, padName, padsByChain } from "../chains";
import { ChainDot } from "./ui";

/** Every launchpad, grouped by chain, with live / coming-soon status. */
export function LaunchpadDirectory({ pads, onPick, picked }: { pads: Pad[]; onPick?: (p: Pad) => void; picked?: string }) {
  return (
    <div>
      {padsByChain(pads).map(({ chain, pads: list }) => (
        <div key={chain.id} className="chain-group">
          <div className="chain-group-head">
            <ChainDot network={chain.id} size={18} />
            {chain.name}
            <span className="muted small" style={{ fontWeight: 400 }}>
              {list.filter((p) => p.status === "available").length} live · {list.length} total
            </span>
          </div>
          <div className="pad-grid">
            {list.map((p) => {
              const live = p.status === "available";
              return (
                <button key={p.id} type="button" className={`pad-tile ${picked === p.id ? "on" : ""}`}
                  disabled={!live || !onPick} style={!onPick && live ? { cursor: "default", opacity: 1 } : undefined}
                  onClick={() => onPick?.(p)}>
                  <span className="grow truncate" style={{ fontWeight: 600 }}>{padName(p.id)}</span>
                  <span className={`badge ${live ? "good" : ""}`}><span className="dot" />{live ? "Live" : "Soon"}</span>
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
