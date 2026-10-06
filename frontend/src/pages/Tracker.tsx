import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../api";
import { ALL_CHAIN_ORDER, chainOf } from "../chains";
import { EVM_ADDR, SOLANA_ADDR } from "../validate";
import { ChainLabel } from "../components/marks";
import {
  Address, Card, Empty, Icon, LoadError, Loading, PageHeader, SearchInput, Seg, TableWrap, useToast,
} from "../components/ui";

interface Item { id: string; address: string; label: string; bookmarked: boolean; network: string }

export default function Tracker() {
  const toast = useToast();
  const [items, setItems] = useState<Item[] | null>(null);
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [network, setNetwork] = useState<string>("solana");
  const [filter, setFilter] = useState<"all" | "starred">("all");
  const [q, setQ] = useState("");

  const [loadErr, setLoadErr] = useState("");
  const load = () => api<Item[]>("/tracker").then((r) => { setItems(r); setLoadErr(""); }).catch((e) => setLoadErr(errMsg(e)));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const act = (p: Promise<unknown>, ok?: string) => p.then(() => { if (ok) toast("good", ok); return load(); }).catch((e) => toast("bad", errMsg(e)));

  function add(e: FormEvent) {
    e.preventDefault();
    act(api("/tracker", { body: { network, address: address.trim(), label: label.trim() } }).then(() => { setAddress(""); setLabel(""); }), "Address added");
  }

  const addr = address.trim();
  // Quick format hints per chain; the server does the full check (checksums included).
  const FORMAT: Record<string, [RegExp, string, string]> = {
    solana: [SOLANA_ADDR, "Solana address", "That doesn't look like a Solana address."],
  };
  const fmtFor = FORMAT[network] ?? [EVM_ADDR, "0x… address", `${chainOf(network).name} addresses start with 0x and have 40 hex characters.`];
  const valid = fmtFor[0].test(addr);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (items ?? [])
      .filter((i) => filter === "all" || i.bookmarked)
      .filter((i) => !s || i.label.toLowerCase().includes(s) || i.address.toLowerCase().includes(s))
      .sort((a, b) => Number(b.bookmarked) - Number(a.bookmarked));
  }, [items, filter, q]);

  return (
    <>
      <PageHeader title="Tracker" sub="Keep an eye on outside wallets on any chain: devs, whales, other traders. Label them and star the ones that matter." />

      <Card title="Track an address" icon="radar">
        <form className="row" onSubmit={add} style={{ alignItems: "stretch" }}>
          <select value={network} onChange={(e) => setNetwork(e.target.value)} style={{ flex: "0 0 150px" }} aria-label="Chain">
            {ALL_CHAIN_ORDER.map((c) => <option key={c} value={c}>{chainOf(c).name}</option>)}
          </select>
          <input className="mono" aria-label="Address to track" aria-invalid={!!addr && !valid} placeholder={fmtFor[1]} value={address} onChange={(e) => setAddress(e.target.value)} style={{ flex: "2 1 280px" }} spellCheck={false} autoComplete="off" />
          <input aria-label="Label" placeholder="Label, e.g. Dev of $CAT" value={label} onChange={(e) => setLabel(e.target.value)} style={{ flex: "1 1 180px" }} />
          <button className="btn" disabled={!valid}><Icon name="plus" size={15} />Track</button>
        </form>
        {addr && !valid && <p className="warn-t small" style={{ marginTop: 8 }} role="status">{fmtFor[2]}</p>}
      </Card>

      <Card flush title={`Tracked addresses`} icon="eye" sub={items ? `${items.length} total · ${items.filter((i) => i.bookmarked).length} starred` : undefined}
        actions={<>
          <Seg value={filter} onChange={setFilter} options={[["all", "All"], ["starred", <><Icon name="star" size={12} /> Starred</>]]} />
          <div style={{ width: 220, maxWidth: "100%" }}><SearchInput aria-label="Search tracked wallets" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </>}>
        {loadErr && !items ? <LoadError what="Tracked addresses" error={loadErr} onRetry={load} /> : !items ? <Loading /> : items.length === 0 ? (
          <Empty icon="radar" title="Nothing tracked yet" text="Paste any Solana or EVM address (Base, BNB Chain, Robinhood Chain, Ethereum) above to start watching it." />
        ) : shown.length === 0 ? <Empty icon="search" title="No matches" /> : (
          <TableWrap><table className="rtable">
            <thead><tr><th style={{ width: 44 }}><span className="sr-only">Starred</span></th><th>Label</th><th>Chain</th><th>Address</th><th className="r"><span className="sr-only">Remove</span></th></tr></thead>
            <tbody>
              {shown.map((i) => (
                <tr key={i.id} className="has-cb">
                  <td className="cb">
                    <button className={`icon-btn ${i.bookmarked ? "on" : ""}`} title={i.bookmarked ? "Unstar" : "Star"} aria-label={i.bookmarked ? `Unstar ${i.label || i.address}` : `Star ${i.label || i.address}`} aria-pressed={i.bookmarked}
                      onClick={() => act(api(`/tracker/${i.id}?bookmarked=${!i.bookmarked}`, { method: "PATCH", body: {} }))}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill={i.bookmarked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
                        <path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z" />
                      </svg>
                    </button>
                  </td>
                  <td style={{ fontWeight: 550 }}>{i.label || <span className="muted">Unlabelled</span>}</td>
                  <td data-label="Chain"><ChainLabel network={i.network} size={16} /></td>
                  <td data-label="Address"><Address address={i.address} chars={6} network={i.network} /></td>
                  <td className="r">
                    <button className="icon-btn" title="Remove" aria-label={`Stop tracking ${i.label || i.address}`} onClick={() => act(api(`/tracker/${i.id}`, { method: "DELETE" }), "Removed")}><Icon name="trash" size={14} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></TableWrap>
        )}
      </Card>
    </>
  );
}
