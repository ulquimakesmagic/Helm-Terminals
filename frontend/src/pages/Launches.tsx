import ChainSelect from "../components/ChainSelect";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errMsg, short } from "../api";
import { ALL_CHAIN_ORDER, CHAIN_ORDER, CHAINS, ChainId, chainOf, Pad, padName } from "../chains";
import { launchpadExperience } from "../launchpadExperience";
import { groupedFields, launchpadForm, validateLaunchFields } from "../launchpadForms";
import { LaunchpadDirectory } from "../components/Launchpads";
import { ChainLabel, PadLabel } from "../components/marks";
import {
  Address, Callout, Card, Empty, Field, Icon, Loading, LoadError, Modal, PageHeader, SearchInput, Seg, Spinner, StatusBadge, TokenImage, UnitInput, relTime,
} from "../components/ui";

interface QuoteAsset { mint: string; symbol: string; name: string; decimals: number; image: string | null; kind: string; pool: string }
interface Launch {
  quote?: { mint: string; symbol: string; name: string } | null;
  id: string; network: string; name: string; symbol: string; status: string; launchpad: string; mint_address: string | null;
  imported: boolean; created_at: string; dev_buy_sol: number; description: string; has_image?: boolean;
  dev_wallet_id?: string | null; socials?: Record<string,string>; settings?: Record<string,unknown>; slippage_pct?: number; draft_revision?: number; clanker_version?:number;
}
interface W { id: string; label: string; address: string; kind: string; network: string }
function optLabel(key: string, o: string | number): string {
  if (key === "tax_rate_pct") return o === 0 ? "No tax (standard token)" : `${o}% tax on buys and sells`;
  if (key === "creator_tax_pct") return o === 0 ? "No creator tax" : `${o}% creator tax on trades`;
  if (key === "lp_share_pct") return o === 100 ? "100% of supply into the pool" : `${o}% into the pool, ${100 - +o}% kept`;
  if (key === "dbc_quote") return o === "SOL" ? "SOL (100 → 400 SOL market cap)" : "USDC ($16k → $69k market cap)";
  if (key === "lp_tokens") return o === "burn" ? "Burn LP tokens (lock liquidity for good)" : "Keep LP tokens (can remove liquidity)";
  if (key === "sniper_start_pct") return `${o}% fee on the first swaps${o === 66.6777 ? " (Clanker default)" : ""}`;
  if (key === "sniper_decay_s") return `falls to ~4% over ${o} seconds${o === 15 ? " (default)" : ""}`;
  if (key === "anti_sniping") return o === "on" ? "On (recommended)" : "Off";
  return String(o);
}

interface OptField {
  key: string; type: "number" | "select" | string; label?: string; hint?: string;
  options?: (string | number)[]; default?: unknown; min?: number; max?: number;
  option_labels?: Record<string,string>;
  asset_kinds?: Record<string,string>;
}
interface PadSchema { fields: OptField[]; socials: string[]; requires_image: boolean; notes: string[] }

type Filter = "all" | "live" | "draft" | "archived";

function toBase64(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] ?? "");
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

const EMPTY_FORM = { launchpad: "pumpfun", name: "", symbol: "", description: "", twitter: "", telegram: "", website: "", dev_wallet_id: "", dev_buy_sol: "0" };

export function CreateLaunch({ pads, wallets, onClose, initial, onSaved }: { pads: Pad[]; wallets: W[]; onClose: () => void; initial?: Launch; onSaved?: (launch:Launch)=>void }) {
  const nav = useNavigate();
  const [f, setF] = useState(initial ? {...EMPTY_FORM, launchpad:initial.launchpad,name:initial.name,symbol:initial.symbol,description:initial.description,dev_wallet_id:initial.dev_wallet_id ?? '',dev_buy_sol:String(initial.dev_buy_sol),twitter:initial.socials?.twitter ?? '',telegram:initial.socials?.telegram ?? '',website:initial.socials?.website ?? ''} : EMPTY_FORM);
  const [image, setImage] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [removeImage, setRemoveImage] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [quotes, setQuotes] = useState<QuoteAsset[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [quoteMint, setQuoteMint] = useState("");
  const [eligible, setEligible] = useState(false);
  const [schema, setSchema] = useState<PadSchema>({ fields: [], socials: [], requires_image: false, notes: [] });
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [schemaReady, setSchemaReady] = useState(false);
  const [quotesLoading, setQuotesLoading] = useState(false);
  const [quotesError, setQuotesError] = useState("");
  const [reloadSettings, setReloadSettings] = useState(0);
  const [step, setStep] = useState(0);
  const [pairSearch,setPairSearch]=useState('');
  const [opts, setOpts] = useState<Record<string, unknown>>({});
  const [slippagePct, setSlippagePct] = useState("10");
  const pad = pads.find((p) => p.id === f.launchpad);
  const quote = quotes.find((q) => q.mint === quoteMint);
  const paired = !!quote && quote.kind !== "native";

  useEffect(() => {
    let active = true;
    setQuoteMint(""); setEligible(false); setQuotes([]); setNotice(null);
    setSchemaLoading(true); setSchemaReady(false); setError(""); setQuotesError(""); setQuotesLoading(false);
    setSchema({ fields: [], socials: [], requires_image: false, notes: [] }); setOpts({});
    if (pad?.status !== "available" && !initial) { setSchemaLoading(false); return; }
    if (f.launchpad === "bags" && !initial?.quote) {
    setQuotesLoading(true);
    api<{ assets: QuoteAsset[]; notice: string | null; error?: string }>(`/launchpads/${f.launchpad}/quote-assets`)
      .then((r) => { if (active) { setQuotes(r.assets); setNotice(r.notice); setQuoteMint(r.assets[0]?.mint ?? ""); if (!r.assets.length) setQuotesError("Bags pair assets are unavailable. Retry before creating a draft."); } })
      .catch((e) => { if (active) setQuotesError(`Pair assets could not be loaded: ${errMsg(e)}`); })
      .finally(() => { if (active) setQuotesLoading(false); });
    }
    api<Partial<PadSchema>>(`/launchpads/${f.launchpad}/options`).then((r) => {
      if (!active) return;
      const fields = r.fields ?? [];
      setSchema({ fields, socials: r.socials ?? [], requires_image: !!r.requires_image, notes: r.notes ?? [] });
      setOpts(Object.fromEntries(fields.filter((x) => !["dev_buy_sol", "slippage_pct"].includes(x.key)).map((x) => [x.key, initial?.settings?.[x.key] ?? x.default])));
      const buy = fields.find((x) => x.key === "dev_buy_sol");
      const slip = fields.find((x) => x.key === "slippage_pct");
      setF((old) => ({ ...old, dev_buy_sol: String(initial?.dev_buy_sol ?? buy?.default ?? 0) }));
      setSlippagePct(String(initial?.slippage_pct ?? slip?.default ?? 10));
      setSchemaReady(true);
      setSchemaLoading(false);
    }).catch((e) => { if (active) { setSchemaLoading(false); setError(`Could not load ${padName(f.launchpad)} settings: ${errMsg(e)}`); } });
    return () => { active = false; };
  }, [f.launchpad, pad?.status, reloadSettings]);
  const chain = chainOf(pad?.network);
  const experience = launchpadExperience(f.launchpad);
  const profile = launchpadForm(f.launchpad);
  const requiresImage = schema.requires_image;
  const selectedQuoteKind=schema.fields.find(field=>field.key==='clanker_quote')?.asset_kinds?.[String(opts.clanker_quote)];
  const noCreatorBuy=!!selectedQuoteKind && !['native','stable'].includes(selectedQuoteKind);
  const selectedPair=f.launchpad.startsWith('clanker') ? (opts.clanker_quote==='native'?chain.native:schema.fields.find(field=>field.key==='clanker_quote')?.option_labels?.[String(opts.clanker_quote)] ?? String(opts.clanker_quote ?? chain.native)) : null;
  const devBuyField = schema.fields.find((x) => x.key === "dev_buy_sol");
  const groups = groupedFields(f.launchpad, schema.fields.filter(field=>field.type !== 'select' || (field.options?.length ?? 0) > 1));
  const validWallets = wallets.filter(w => w.network === chain.id && w.kind === "managed");
  const values = { ...opts, dev_buy_sol: paired ? 0 : f.dev_buy_sol, slippage_pct: slippagePct };
  const validation = schemaReady ? validateLaunchFields(schema.fields, values) : null;
  const blocked = !schemaReady || schemaLoading || quotesLoading || !!quotesError || !!validation;
  const tokenReady = !!f.name.trim() && !!f.symbol.trim() && (!requiresImage || !!image || !!initial?.has_image && !removeImage) && (!image || image.size <= 2_000_000);
  const ready = tokenReady && !blocked && (!!initial || pad?.status === "available") && (!paired || eligible);
  const pickPad = (id:string) => { if (id !== f.launchpad) { setSchemaReady(false); setF(old => ({...old,launchpad:id})); setStep(0); } };
  useEffect(() => {
    setF(old => old.dev_wallet_id && !wallets.some(w => w.id === old.dev_wallet_id && w.network === chain.id && w.kind === "managed") ? { ...old, dev_wallet_id: "" } : old);
  }, [chain.id, wallets]);
  const socials = new Set(schema.socials);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: k === "symbol" ? e.target.value.toUpperCase() : e.target.value });

  useEffect(() => {
    if (!image) { setPreview(null); return; }
    const u = URL.createObjectURL(image);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [image]);

  async function create() {
    setError("");
    if (!ready || busy) return;
    if (image && image.size > 2_000_000) { setError("Image must be 2 MB or smaller."); return; }
    setBusy(true);
    try {
      const l = await api<Launch>(initial ? `/launches/${initial.id}` : "/launches", { method:initial?'PATCH':'POST',body: {
        launchpad: f.launchpad, name: f.name.trim(), symbol: f.symbol.trim(), description: f.description,
        socials: { twitter: f.twitter, telegram: f.telegram, website: f.website },
        dev_wallet_id: f.dev_wallet_id || null, dev_buy_sol: paired ? 0 : +f.dev_buy_sol || 0,
        slippage_pct: +slippagePct || 0,
        image_b64: image ? await toBase64(image) : null,
        quote_mint: paired ? quoteMint : null, eligibility_confirmed: paired ? eligible : false,
        options: opts,
        ...(initial ? {expected_revision:initial.draft_revision ?? 0,remove_image:removeImage && !image}:{}),
      } });
      if(initial){onSaved?.(l);onClose();}else nav(`/launches/${l.id}`);
    } catch (e) { setError(errMsg(e)); setBusy(false); }
  }

  return (
    <Modal wide title={initial?'Edit draft':experience.title} sub={`${chain.name} · ${experience.mode}. Save a draft, then review transactions before signing.`} onClose={onClose} lock={busy}
      foot={<>
        <span className="muted small grow">{schemaLoading ? "Loading launchpad settings…" : `Step ${step+1} of 3 · No transaction is sent`}</span>
        <button className="btn ghost" disabled={busy} onClick={onClose}>Cancel</button>
        {step > 0 && <button type="button" className="btn secondary" disabled={busy} onClick={()=>setStep(step-1)}>Back</button>}
        {step < 2 ? <button type="button" className="btn" disabled={busy || schemaLoading || (step === 0 ? !tokenReady : !ready)} onClick={()=>setStep(step+1)}>{step === 0 ? "Continue" : "Review draft"}<Icon name="arrowright" size={15}/></button> : <button className="btn" disabled={busy || !ready} onClick={create}>{busy ? <Spinner /> : <Icon name="check" size={15} />}{initial?'Save draft':'Create draft'}</button>}
      </>}>
      {error && <Callout tone="err">{error}</Callout>}
      {quotesError && <Callout tone="err">{quotesError}</Callout>}
      {(!schemaReady && !schemaLoading && pad?.status === "available" || quotesError) && <button className="btn ghost" onClick={() => setReloadSettings(n => n + 1)}>Retry launchpad settings</button>}
      {initial ? <div className="row small"><ChainLabel network={chain.id}/><span>·</span><PadLabel pad={initial.launchpad}/><span className="muted">Original launch route</span></div> : <div className="launch-route"><ChainSelect value={chain.id} includeAll={false} label="Launch network" networks={ALL_CHAIN_ORDER.filter(n=>pads.some(p=>p.network===n && p.status==='available'))} onChange={network=>{const next=pads.find(p=>p.network===network && p.status==='available');if(next)pickPad(next.id);}}/><Field label="Launchpad"><select aria-label="Launchpad" value={f.launchpad} onChange={e=>pickPad(e.target.value)}>{pads.filter(p=>p.network===chain.id).map(p=><option key={p.id} value={p.id} disabled={p.status!=='available'}>{padName(p.id)}{p.status!=='available'?' · unavailable':''}</option>)}</select></Field></div>}
      <nav className="launch-steps" aria-label="Launch steps">{['Token','Launch settings','Review'].map((label,index)=><button key={label} type="button" aria-current={step===index?'step':undefined} disabled={busy || index>step && !(index===1?tokenReady:ready)} onClick={()=>setStep(index)}><span>{index+1}</span>{label}</button>)}</nav>
      {step === 0 && <>
      <div className="launch-identity">
        <label style={{ cursor: "pointer", flex: "none" }} title="Upload image">
          <input type="file" aria-label="Token artwork" accept="image/png,image/jpeg,image/gif,image/webp" className="sr-only" onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
          {preview ? (
            <img src={preview} alt="" style={{ width: 112, height: 112, borderRadius: 18, objectFit: "cover", border: "1px solid var(--line-2)" }} />
          ) : initial?.has_image && !removeImage ? <TokenImage launch={initial} size={112}/> : (
            <div style={{ width: 112, height: 112, borderRadius: 18, border: "1.5px dashed var(--line-2)", display: "grid", placeItems: "center", color: "var(--muted)", textAlign: "center", fontSize: 11.5 }}>
              <div><Icon name="image" size={22} /><div style={{ marginTop: 6 }}>Add image<br />{requiresImage ? <b className="warn-t">required</b> : "max 2 MB"}</div></div>
            </div>
          )}
        </label>
        <div className="stack grow">
          <div className="launch-identity-fields">
            <Field label="Token name" extra={`${f.name.length}/32`} style={{ flex: 2 }}><input value={f.name} onChange={set("name")} maxLength={32} placeholder="Helm Cat" autoFocus /></Field>
            <Field label="Ticker" style={{ flex: 1 }}><input value={f.symbol} onChange={set("symbol")} maxLength={10} placeholder="HCAT" className="mono" /></Field>
          </div>
          <div className="row small muted" style={{ gap: 8 }}>
            Launching on <ChainLabel network={chain.id} size={16} /> via <PadLabel pad={f.launchpad} network={chain.id} size={18} />
          </div>
        </div>
      </div>
      {(image || initial?.has_image && !removeImage) && <button type="button" className="btn sm ghost" onClick={()=>{setImage(null);setRemoveImage(true);}}>Remove artwork</button>}
      <Field label="Description" extra={`${f.description.length}/500`}>
        <textarea value={f.description} onChange={set("description")} rows={3} maxLength={500} placeholder="What is this token about?" />
      </Field>
      {schema.socials.length > 0 && <details className="launch-links"><summary>Links <span className="muted">optional</span></summary><div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        {socials.has("twitter") && <Field label="X / Twitter"><input value={f.twitter} onChange={set("twitter")} placeholder="https://x.com/…" /></Field>}
        {socials.has("telegram") && <Field label="Telegram"><input value={f.telegram} onChange={set("telegram")} placeholder="https://t.me/…" /></Field>}
        {socials.has("website") && <Field label="Website"><input value={f.website} onChange={set("website")} placeholder="https://…" /></Field>}
      </div></details>}
      </>}
      {step === 1 && <>
      <div className="section-heading"><div><h3>{experience.settingsTitle}</h3><p className="muted small">{experience.description}</p></div></div>
      {schemaLoading && <p role="status" className="muted"><Spinner /> Loading this launchpad's controls…</p>}
      {initial?.launchpad.startsWith('clanker') && initial.clanker_version!==5 && <Callout>New Clanker launches use V5. Saving this undeployed draft replaces the V4 sniper settings with the V5 tax, pair and reward settings below. Live tokens keep their original terms.</Callout>}
      {groups.map(group => <section className="launch-form-section" key={group.title}>
        <h4>{group.title}</h4><p className="muted small">{group.description}</p>
        {group.fields.some(x=>x.key==='clanker_quote') && <Field label="Find a pair asset" hint="Search by symbol, name or address. Assets with the same symbol remain distinct by address."><input type="search" value={pairSearch} onChange={e=>setPairSearch(e.target.value)} placeholder="ETH, USDC, stock symbol or address"/></Field>}
        <div className="grid g-form">{group.fields.filter(x=>x.key!=='quote_eligibility' || selectedQuoteKind==='stock').map(x => <Field key={x.key} label={x.label ?? (x.key === "priority_fee_sol" ? "Priority fee (SOL)" : x.key === "slippage_pct" ? "Maximum slippage (%)" : x.key.replace(/_/g, " "))} hint={x.hint}>
          {x.type === "select" ? <select value={String(opts[x.key] ?? "")} onChange={e => {setOpts({ ...opts, [x.key]: x.options?.find(o => String(o) === e.target.value),...(x.key==='clanker_quote'?{quote_eligibility:'not_confirmed'}:{}) });if(x.key==='clanker_quote' && e.target.value.startsWith('0x'))setF(old=>({...old,dev_buy_sol:'0'}));}}>
            {(x.options ?? []).filter(o=>x.key!=='clanker_quote'||o===opts.clanker_quote||(String(o)+' '+(x.option_labels?.[String(o)]??'')).toLowerCase().includes(pairSearch.toLowerCase())).map(o => <option key={String(o)} value={String(o)}>{x.option_labels?.[String(o)] ?? optLabel(x.key, o)}</option>)}
          </select> : <input type="number" min={x.min} max={x.max} step="any" value={String(x.key === "slippage_pct" ? slippagePct : opts[x.key] ?? "")} onChange={e => x.key === "slippage_pct" ? setSlippagePct(e.target.value) : setOpts({ ...opts, [x.key]: e.target.value === "" ? "" : +e.target.value })} />}
        </Field>)}</div>
        {group.fields.some(x => x.key === "tax_rate_pct") && <p className="small">{Number(opts.tax_rate_pct) > 0 ? `${opts.tax_rate_pct}% buy tax · ${opts.tax_rate_pct}% sell tax · one year · creator allocation to the dev wallet` : "Standard token · no creator trading tax"}</p>}
      </section>)}
      {quotes.length > 1 && (
        <>
          <div className="divider" />
          <h3>Pair asset</h3>
          <div className="chips">
            {quotes.map((q) => (
              <button key={q.mint} type="button" disabled={q.kind !== "native"} aria-pressed={quoteMint === q.mint} className={`chip ${quoteMint === q.mint ? "on" : ""}`} onClick={() => { setQuoteMint(q.mint); setEligible(false); }} title={q.kind !== "native" ? `${q.name} · new pair launches are not available yet` : q.name}>
                {q.image ? <img src={q.image} alt="" style={{ width: 16, height: 16, borderRadius: "50%" }} /> : null}
                {q.symbol}
              </button>
            ))}
          </div>
          <Callout>New custom-pair launches are unavailable while Helm integrates Bags' updated launch and reward flow. SOL launches remain available.</Callout>
          {paired && quote && (
            <div className="stack tight">
              <Callout>
                <b>{f.symbol || "Your token"}/{quote.symbol} pool.</b> {quote.pool}. Liquidity is locked for good, and a 2% pool fee accrues in {quote.symbol}.
                Buyers pay in {quote.symbol} (Helm routes SOL buys through it where a route exists). No dev buy for pair launches yet, and pool fees are claimed on bags.fm for now.
              </Callout>
              {(
                <label className="row nowrap" style={{ gap: 10, alignItems: "flex-start", cursor: "pointer", fontSize: 13 }}>
                  <input type="checkbox" checked={eligible} onChange={(e) => setEligible(e.target.checked)} style={{ marginTop: 2 }} />
                  <span className="text-2 grow" style={{ whiteSpace: "normal" }}>{notice ?? "Check the selected asset's eligibility requirements before using this pair."} <b>I confirm I'm eligible</b> and that I'm not creating this for anyone who isn't.</span>
                </label>
              )}
            </div>
          )}
        </>
      )}
      <h4>{devBuyField && devBuyField.max !== 0 ? profile.funding : 'Launch wallet'}</h4>
      <div className="launch-funding">
        <Field label="Launch wallet" style={{ flex: 2 }} hint={validWallets.length === 0 ? `Create a managed ${chain.name} wallet on the Wallets page first.` : f.launchpad === 'meme-cooking' ? 'Creates the auction. An optional deposit participates in the auction; it is not an immediate token purchase.' : f.launchpad === 'uniswap-v2' ? 'Creates the token and pool, and supplies the starting liquidity. This is not a creator trade.' : 'Signs this launch. Network and launchpad costs are shown before you confirm deployment.'}>
          <select aria-label="Launch wallet" value={f.dev_wallet_id} onChange={set("dev_wallet_id")}>
            <option value="">Choose later…</option>
            {validWallets.map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {short(w.address)}</option>)}
          </select>
        </Field>
        {!paired && devBuyField && devBuyField.max !== 0 && <Field label={devBuyField.label?.replace(/\s*\(.*\)$/, "") ?? "Dev buy"} hint={noCreatorBuy?'This quote has no verified creator-buy route in Helm. Launch without a creator buy.':devBuyField.hint} style={{ flex: 1 }}><UnitInput disabled={noCreatorBuy} type="number" min={devBuyField.min} max={devBuyField.max} step="any" unit={chain.native} value={f.dev_buy_sol} onChange={set("dev_buy_sol")} /></Field>}
      </div>
      {validation && <Callout tone="err">{validation}</Callout>}
      {schema.notes.map((note) => <Callout key={note}>{note}</Callout>)}
      <details className="launch-links"><summary>Supported route details</summary><p className="muted small">{profile.scope}</p></details>
      </>}
      {step === 2 && <>
      <div className="reward-disclosure"><div><Icon name="coins" size={17} /><b>{experience.rewardTitle}</b></div><p>{experience.rewardCopy}</p></div>
      <p className="muted small">After creating the draft, plan the token's allocation and vesting in its Launchdesk. Any
        anti-sniping settings above are explained again, with their effect and fees, in the deploy preview.</p>
      <section className="launch-form-section launch-summary" aria-label="Draft summary">
        <div className="row">{!preview && initial?.has_image && !removeImage && <TokenImage launch={initial}/>} {preview && <img src={preview} alt="Token preview" width={40} height={40} style={{ borderRadius: 10, objectFit: "cover" }} />}<div><h3>{f.name.trim() || "Your token"} <span className="muted">{f.symbol ? `$${f.symbol}` : ""}</span></h3><span className="small muted">{chain.name} · {padName(f.launchpad)}</span></div></div>
        <dl><div><dt>Pair</dt><dd>{selectedPair ?? quote?.symbol ?? (opts.dbc_quote ? String(opts.dbc_quote) : profile.pair === "Native asset" ? chain.native : profile.pair)}</dd></div><div><dt>{profile.funding}</dt><dd>{paired || noCreatorBuy ? "Not supported for this pair" : `${f.dev_buy_sol || "0"} ${chain.native}`}</dd></div><div><dt>Dev wallet</dt><dd>{validWallets.find(w => w.id === f.dev_wallet_id)?.label || (f.dev_wallet_id ? short(validWallets.find(w => w.id === f.dev_wallet_id)?.address ?? "") : "Choose before deployment")}</dd></div></dl>
        <p className="muted small">Draft only. Network costs, platform fees and reward recipients are reviewed before signing.</p>
      </section>
      </>}
    </Modal>
  );
}

/** Copy an existing token's metadata (name, ticker, description, image, socials) into a new draft for relaunch. */
function ImportLaunch({ wallets, pads, onClose }: { wallets: W[]; pads: Pad[]; onClose: () => void }) {
  const nav = useNavigate();
  const usable = pads.filter((p) => p.status === "available");
  const [source, setSource] = useState<ChainId>("solana");
  const [mint, setMint] = useState("");
  const [pad, setPad] = useState(usable.find((p) => p.id === "pumpfun")?.id ?? usable[0]?.id ?? "pumpfun");
  const [dev, setDev] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const target = chainOf(pads.find((p) => p.id === pad)?.network);
  const devs = wallets.filter((w) => w.kind === "managed" && w.network === target.id);
  useEffect(() => { if (dev && !devs.some((w) => w.id === dev)) setDev(""); }, [pad]); // eslint-disable-line react-hooks/exhaustive-deps
  async function go() {
    setBusy(true); setError("");
    try {
      const l = await api<Launch>("/launches/clone", { body: { mint: mint.trim(), network: source, launchpad: pad, dev_wallet_id: dev || null } });
      nav(`/launches/${l.id}`);
    } catch (e) { setError(errMsg(e)); setBusy(false); }
  }
  return (
    <Modal title="Import or clone a token" sub="Copies an existing token's details into a new draft for you to relaunch" onClose={onClose}
      foot={<><button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!mint.trim() || !pad || busy} onClick={go}>{busy && <Spinner />}Copy into draft</button></>}>
      <p className="muted small">
        Helm copies the name, ticker, description, image and social links it can find, into a draft you can edit. Nothing is
        deployed until you review and confirm the draft's deployment. On EVM chains only the name and ticker are on-chain.
      </p>
      {error && <Callout tone="err">{error}</Callout>}
      <div className="grid g-form">
        <Field label="Token is on">
          <select value={source} onChange={(e) => setSource(e.target.value as ChainId)}>
            {CHAIN_ORDER.map((c) => <option key={c} value={c}>{CHAINS[c].name}</option>)}
          </select>
        </Field>
        <Field label="Token address">
          <input value={mint} onChange={(e) => setMint(e.target.value)} className="mono" placeholder={source === "solana" ? "e.g. 7xKX…pump" : "0x…"} autoFocus spellCheck={false} />
        </Field>
      </div>
      <div className="grid g-form">
        <Field label="Relaunch on" hint={<span className="row nowrap" style={{ gap: 6 }}>Deploys on <ChainLabel network={target.id} size={14} /></span>}>
          <select value={pad} onChange={(e) => setPad(e.target.value)}>
            {usable.map((p) => <option key={p.id} value={p.id}>{padName(p.id)} · {chainOf(p.network).name}</option>)}
          </select>
        </Field>
        <Field label="Dev wallet (optional)">
          <select value={dev} onChange={(e) => setDev(e.target.value)}>
            <option value="">Choose later…</option>
            {devs.map((w) => <option key={w.id} value={w.id}>{w.label || "Wallet"} · {short(w.address)}</option>)}
          </select>
        </Field>
      </div>
    </Modal>
  );
}

/** Delete control for a draft that never ran anything. Two steps, no browser dialog. */
export function DeleteDraft({ id, name, onDeleted, compact }: { id: string; name: string; onDeleted: () => void; compact?: boolean }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  if (!ask) return (
    <button type="button" className={`btn ${compact ? "sm ghost del-icon" : "ghost danger-text"}`} onClick={(e) => { stop(e); setAsk(true); }} onKeyDown={stop}
      aria-label={`Delete draft ${name}`} title="Delete draft"><Icon name="trash" size={13} />{compact ? <span className="sr-only">Delete draft</span> : "Delete draft"}</button>
  );
  return (
    <span className="row nowrap confirm-del" onClick={stop} onKeyDown={stop} role="group" aria-label={`Confirm deleting ${name}`}>
      <span className="small">{err || "Delete this draft?"}</span>
      <button type="button" className="btn sm danger" disabled={busy} autoFocus onClick={() => {
        setBusy(true);
        api(`/launches/${id}`, { method: "DELETE" }).then(onDeleted).catch((e) => { setErr(errMsg(e)); setBusy(false); });
      }}>{busy ? <Spinner /> : "Delete"}</button>
      <button type="button" className="btn sm ghost" onClick={() => { setAsk(false); setErr(""); }}>Cancel</button>
    </span>
  );
}

export default function Launches() {
  const nav = useNavigate();
  const [launches, setLaunches] = useState<Launch[] | null>(null);
  const [archived, setArchived] = useState<Launch[]>([]);
  const [pads, setPads] = useState<Pad[]>([]);
  const [wallets, setWallets] = useState<W[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [chainF, setChainF] = useState<"all" | ChainId>("all");
  const [modal, setModal] = useState<null | "create" | "import">(null);

  const [loadErr, setLoadErr] = useState("");
  const load = useCallback(() => {
    setLoadErr("");
    Promise.all([api<Launch[]>("/launches"), api<Launch[]>("/launches?status=archived"), api<Pad[]>("/launchpads"), api<W[]>("/wallets")])
      .then(([l, a, p, w]) => { setLaunches(l); setArchived(a); setPads(p); setWallets(w.filter((x) => x.kind === "managed")); })
      .catch((e) => setLoadErr(errMsg(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => ({
    all: launches?.length ?? 0,
    live: launches?.filter((l) => l.status === "live").length ?? 0,
    draft: launches?.filter((l) => l.status !== "live").length ?? 0,
    archived: archived.length,
  }), [launches, archived]);

  const shown = useMemo(() => {
    const base = filter === "archived" ? archived : (launches ?? []).filter((l) => filter === "all" || (filter === "live" ? l.status === "live" : l.status !== "live"));
    const s = q.trim().toLowerCase();
    const byChain = chainF === "all" ? base : base.filter((l) => (l.network ?? "solana") === chainF);
    return s ? byChain.filter((l) => l.name.toLowerCase().includes(s) || l.symbol.toLowerCase().includes(s) || (l.mint_address ?? "").toLowerCase().includes(s)) : byChain;
  }, [launches, archived, filter, q, chainF]);

  return (
    <>
      <PageHeader title="Launches" sub="Your tokens, from first draft to live operation. Pick up where you left off, or start something new."
        actions={<>
          <button className="btn secondary" onClick={() => setModal("import")}><Icon name="download" size={15} />Import / clone</button>
          <button className="btn" onClick={() => setModal("create")}><Icon name="plus" size={15} />New launch</button>
        </>} />

      <div className="launch-filter-bar">
        <Seg value={filter} onChange={setFilter} options={[
          ["all", <>All <span className="muted num">{counts.all}</span></>],
          ["live", <>Live <span className="muted num">{counts.live}</span></>],
          ["draft", <>Drafts <span className="muted num">{counts.draft}</span></>],
          ["archived", <>Archived <span className="muted num">{counts.archived}</span></>],
        ]} />
        <ChainSelect value={chainF} onChange={v => setChainF(v as "all" | ChainId)} networks={ALL_CHAIN_ORDER.filter(c => (CHAIN_ORDER as string[]).includes(c) || pads.some(p => p.network === c))} />
        <div style={{ width: 280, maxWidth: "100%" }}><SearchInput aria-label="Search launches by name, ticker or token address" placeholder="Search name, ticker or mint" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>

      {loadErr && !launches ? <div className="card"><LoadError what="Launches" error={loadErr} onRetry={load} /></div> : !launches ? <div className="card"><Loading rows={4} /></div> : shown.length === 0 && (q || filter !== "all") ? (
        <div className="card"><Empty icon="search" title="No launches match" text="Try a different filter or search." /></div>
      ) : (
        <div className="grid g-cards">
          {shown.length === 0 && filter !== "archived" && !q && (
            <button className="card lcard-new" onClick={() => setModal("create")}>
              <span className="empty" style={{ padding: 0 }}><span className="ic"><Icon name="plus" size={22} /></span></span>
              <b style={{ color: "var(--text)" }}>New launch</b>
              <span className="small">Pick a chain and launchpad</span>
            </button>
          )}
          {shown.map((l) => (
            <div key={l.id} className="card lcard" role="link" tabIndex={0}
              onClick={() => nav(`/launches/${l.id}`, { state: { launch: l } })}
              onKeyDown={(e) => e.key === "Enter" && nav(`/launches/${l.id}`, { state: { launch: l } })}>
              <div className="row nowrap" style={{ gap: 12 }}>
                <TokenImage launch={l} size={42} />
                <div className="grow">
                  <div className="truncate" style={{ fontWeight: 650, fontSize: 15 }}>{l.name}</div>
                  <div className="muted mono" style={{ fontSize: 12 }}>${l.symbol}</div>
                </div>
                <StatusBadge status={l.status} />
              </div>
              {(l.status === "draft" || l.imported) && (
                <div className="lcard-del"><DeleteDraft id={l.id} name={l.name} compact onDeleted={load} /></div>
              )}
              {l.description && <p className="muted small" style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{l.description}</p>}
              <div className="meta">
                <div><span className="k">Chain</span><ChainLabel network={l.network} size={16} /></div>
                <div><span className="k">Launchpad</span><span className="row nowrap" style={{ gap: 6 }}><PadLabel pad={l.launchpad} network={l.network} size={18} />{l.quote && <span className="badge info">/{l.quote.symbol}</span>}{l.imported && <span className="badge violet">imported</span>}</span></div>
                <div><span className="k">Created</span>{relTime(l.created_at)}</div>
                <div><span className="k">Token address</span>{l.mint_address ? <Address address={l.mint_address} type="token" network={l.network} /> : <span className="muted">Not deployed</span>}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {pads.length > 0 && (
        <details className="launchpad-discovery"><summary>Explore launchpads<span className="muted small">Availability and supported networks</span></summary><Card title="Launchpads" icon="globe" sub={`${pads.length} launchpads across ${new Set(pads.map((p) => p.network)).size} chains · ${pads.filter((p) => p.status === "available").length} live today, the rest rolling out`}>
          <LaunchpadDirectory pads={pads} />
        </Card></details>
      )}

      {modal === "create" && <CreateLaunch pads={pads} wallets={wallets} onClose={() => setModal(null)} />}
      {modal === "import" && <ImportLaunch wallets={wallets} pads={pads} onClose={() => setModal(null)} />}
    </>
  );
}
