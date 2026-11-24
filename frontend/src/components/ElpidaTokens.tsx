import { useEffect, useState } from "react";
import { api, errMsg } from "../api";
import { chainOf, Pad } from "../chains";
import { Address, Callout, Card, Field, Icon, Loading, TokenImage } from "./ui";
import { PadLabel } from "./marks";

interface DiscoveredToken {
  address: string;
  network: string;
  launchpad: string;
  name: string;
  symbol: string;
  image: string | null;
  description: string;
  socials: Record<string, string>;
  url: string;
}

export function ElpidaTokens({ pads, onOpen, initialNetwork, initialAddress }: {
  pads: Pad[];
  onOpen: (id: string) => void;
  initialNetwork?: string | null;
  initialAddress?: string | null;
}) {
  const [open, setOpen] = useState(Boolean(initialNetwork));
  const [network, setNetwork] = useState(initialNetwork ?? pads[0].network);
  const [address, setAddress] = useState(initialAddress ?? "");
  const [rows, setRows] = useState<DiscoveredToken[] | null>(null);
  const [feedError, setFeedError] = useState("");
  const [actionError, setActionError] = useState("");
  const [opening, setOpening] = useState(false);
  const pad = pads.find(p => p.network === network) ?? pads[0];

  useEffect(() => {
    if (!open) return;
    let active = true;
    setRows(null);
    setFeedError("");
    api<DiscoveredToken[]>("/launchpads/" + pad.id + "/tokens").then(data => {
      if (active) setRows(data);
    }).catch(error => {
      if (active) setFeedError(errMsg(error));
    });
    return () => { active = false; };
  }, [open, pad.id]);

  async function openToken(token: string) {
    setOpening(true);
    setActionError("");
    try {
      const result = await api<{id: string}>("/launchpads/" + pad.id + "/tokens/" + encodeURIComponent(token) + "/workspace", {body: {}});
      onOpen(result.id);
    } catch (error) {
      setActionError(errMsg(error));
    } finally {
      setOpening(false);
    }
  }

  return <details className="launchpad-discovery" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Explore Elpida tokens</summary>
    {open && <Card title="Elpida tokens" icon="globe">
      <form className="launch-funding" onSubmit={event => { event.preventDefault(); void openToken(address.trim()); }}>
        <Field label="Network">
          <select value={pad.network} onChange={event => { setNetwork(event.target.value); setAddress(""); setActionError(""); }}>
            {pads.map(p => <option key={p.id} value={p.network}>{chainOf(p.network).name}</option>)}
          </select>
        </Field>
        <Field label="Token address" style={{flex: 1}}>
          <input value={address} onChange={event => setAddress(event.target.value)} placeholder="0x…" spellCheck={false} />
        </Field>
        <button className="btn secondary" disabled={opening || !/^0x[0-9a-fA-F]{40}$/.test(address.trim())}>Open workspace</button>
      </form>
      {actionError && <Callout tone="err">{actionError}</Callout>}
      {feedError ? <Callout tone="err">{feedError}</Callout> : rows === null ? <Loading rows={3} /> :
        rows.length === 0 ? <p className="muted small">No launches in the scanner feed for this network. You can open a token by address.</p> :
        <div className="grid g-cards">
          {rows.map(row => <article className="card lcard" key={row.address}>
            <div className="row">
              <TokenImage launch={{ id: row.address, symbol: row.symbol, image_url: row.image }} />
              <div className="grow"><b>{row.name}</b><div className="muted mono small">{"$" + row.symbol}</div></div>
              <PadLabel pad={row.launchpad} network={row.network} />
            </div>
            {row.description && <p className="small muted">{row.description}</p>}
            <div className="row small"><Address address={row.address} type="token" network={row.network} />
              <a href={row.url} target="_blank" rel="noreferrer">Elpida <Icon name="ext" size={12} /></a>
              {Object.entries(row.socials).map(([name, href]) => <a key={name} href={href} target="_blank" rel="noreferrer">{name}</a>)}
            </div>
            <button className="btn secondary" disabled={opening} onClick={() => void openToken(row.address)}>Open workspace</button>
          </article>)}
        </div>}
    </Card>}
  </details>;
}
