import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, fmt } from "../api";
import { chainOf } from "../chains";
import { ChainMark, PadLabel } from "../components/marks";
import ReferralCard from "../components/Referrals";
import { AVATAR_HUES, avatarBg, Heatmap, PadBars, PadRow, ProfileAvatar } from "../components/profile";
import {
  Address, Callout, Card, CopyButton, Empty, Field, Icon, IconName, Loading, Modal, PageHeader, parseDate, relTime, Skeleton, Spinner, Stat, StatusBadge, TableWrap, useToast,
} from "../components/ui";
import { useSession } from "../session";

export interface ProfileInfo {
  display_name: string; handle: string | null; bio: string; x_handle: string; telegram_handle: string; website: string;
  avatar_hue: number; is_public: boolean; public_path: string | null;
}

interface ProfileData {
  profile: ProfileInfo;
  account: { email: string; created_at: string; totp_enabled: boolean; telegram_linked: boolean; active_sessions: number; referred: boolean; last_sign_in: string | null };
  stats: {
    launches: { total: number; live: number; failed: number; draft: number; archived: number; imported: number };
    launchpads: PadRow[];
    networks: { network: string; launches: number; wallets: number }[];
    trades: { count: number; buys: number; sells: number; volume_sol: number };
    net_sol_flow: number; fees_paid_sol: number;
    wallets: { managed: number; watch: number }; tracked: number;
  };
  heatmap: { start: string; days: number[]; total: number; active_days: number; streak: number };
  milestones: { id: string; label: string; hint: string; done: boolean }[];
  recent_launches: any[];
  referral: any;
}

const MILESTONE_ICON: Record<string, IconName> = {
  secured: "shield", telegram: "telegram", first_launch: "rocket", multichain: "globe", referrer: "gift", public: "eye",
};

export const monthYear = (s: string) => parseDate(s).toLocaleDateString(undefined, { month: "long", year: "numeric" });
const hostOf = (url: string) => { try { return new URL(url).host.replace(/^www\./, ""); } catch { return url; } };

export function ProfileLinks({ p }: { p: Pick<ProfileInfo, "x_handle" | "telegram_handle" | "website"> }) {
  return (
    <>
      {p.x_handle && <a href={`https://x.com/${p.x_handle}`} target="_blank" rel="noreferrer noopener"><Icon name="link" size={13} />@{p.x_handle} on X</a>}
      {p.telegram_handle && <a href={`https://t.me/${p.telegram_handle}`} target="_blank" rel="noreferrer noopener"><Icon name="telegram" size={13} />@{p.telegram_handle}</a>}
      {p.website && <a href={p.website} target="_blank" rel="noreferrer noopener nofollow"><Icon name="globe" size={13} />{hostOf(p.website)}</a>}
    </>
  );
}

function EditProfile({ p, onClose, onSaved }: { p: ProfileInfo; onClose: () => void; onSaved: (p: ProfileInfo) => void }) {
  const toast = useToast();
  const [f, setF] = useState({
    display_name: p.display_name, handle: p.handle ?? "", bio: p.bio, x_handle: p.x_handle,
    telegram_handle: p.telegram_handle, website: p.website, avatar_hue: p.avatar_hue, is_public: p.is_public,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));
  const handle = f.handle.trim().replace(/^@/, "").toLowerCase();

  const save = () => {
    setBusy(true); setError("");
    api<ProfileInfo>("/profile", { method: "PATCH", body: { ...f, handle, is_public: handle ? f.is_public : false } })
      .then((np) => { toast("good", "Profile saved"); onSaved(np); })
      .catch((e) => setError(errMsg(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Modal title="Edit profile" sub="Your name and links. Private unless you turn on the public page." onClose={onClose} lock={busy} wide
      foot={<><button className="btn secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn" onClick={save} disabled={busy || !f.display_name.trim()}>{busy && <Spinner />}Save profile</button></>}>
      {error && <Callout tone="err">{error}</Callout>}
      <div className="row" style={{ gap: 16, alignItems: "center" }}>
        <ProfileAvatar name={f.display_name || "?"} hue={f.avatar_hue} size={64} />
        <div className="stack" style={{ gap: 8 }}>
          <div className="small muted">Avatar colour</div>
          <div className="hue-row">
            {AVATAR_HUES.map((h) => (
              <button key={h} type="button" className={`hue ${f.avatar_hue === h ? "on" : ""}`} style={{ background: avatarBg(h) }}
                onClick={() => set("avatar_hue", h)} aria-label={`Avatar colour ${h}`} aria-pressed={f.avatar_hue === h} />
            ))}
          </div>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14 }}>
        <Field label="Display name"><input value={f.display_name} maxLength={40} onChange={(e) => set("display_name", e.target.value)} /></Field>
        <Field label="Handle" hint="3–20 characters: lowercase letters, numbers and _">
          <div className="handle-input"><span>helmterminals.xyz/u/</span>
            <input value={f.handle} maxLength={21} placeholder="yourname" onChange={(e) => set("handle", e.target.value.replace(/[^a-zA-Z0-9_@]/g, ""))} />
          </div>
        </Field>
      </div>
      <Field label="Bio" extra={<span className="muted small num">{f.bio.length}/280</span>}>
        <textarea rows={3} maxLength={280} value={f.bio} placeholder="What you build, what you launch." onChange={(e) => set("bio", e.target.value)} />
      </Field>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14 }}>
        <Field label="X username"><input value={f.x_handle} placeholder="@username" maxLength={16} onChange={(e) => set("x_handle", e.target.value)} /></Field>
        <Field label="Telegram username"><input value={f.telegram_handle} placeholder="@username" maxLength={33} onChange={(e) => set("telegram_handle", e.target.value)} /></Field>
        <Field label="Website"><input value={f.website} placeholder="https://" maxLength={120} onChange={(e) => set("website", e.target.value)} /></Field>
      </div>
      <label className="row nowrap" style={{ gap: 10, alignItems: "flex-start", cursor: handle ? "pointer" : "not-allowed", opacity: handle ? 1 : 0.6 }}>
        <input type="checkbox" checked={!!handle && f.is_public} disabled={!handle} onChange={(e) => set("is_public", e.target.checked)} style={{ marginTop: 3, width: "auto" }} />
        <span className="grow small text-2" style={{ whiteSpace: "normal" }}>
          <b style={{ color: "var(--text)" }}>Public creator page</b>{handle ? <> at <span className="mono">/u/{handle}</span></> : " (pick a handle first)"}.
          It shows your name, bio, links and the tokens you deployed through Helm. It never shows your email, wallets, balances or P&amp;L.
          Token addresses are public on-chain, so anyone can look up the wallet that created them.
        </span>
      </label>
    </Modal>
  );
}

export default function Profile() {
  const { refresh } = useSession();
  const [d, setD] = useState<ProfileData | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);

  const load = () => api<ProfileData>("/profile").then((x) => { setD(x); setError(""); }).catch((e) => setError(errMsg(e)));
  useEffect(() => { load(); }, []);

  const p = d?.profile;
  const s = d?.stats;
  const publicUrl = p?.public_path ? `${window.location.origin}${p.public_path}` : null;
  const done = d?.milestones.filter((m) => m.done).length ?? 0;

  return (
    <>
      <PageHeader title="Profile" sub="Your account, your track record across chains, and how others see you." />
      {error && <Callout tone="err">{error}</Callout>}

      <Card className="profile-hero" flush>
        <div className="banner" style={{ background: p ? `linear-gradient(120deg, hsl(${p.avatar_hue} 35% 22%), hsl(${(p.avatar_hue + 50) % 360} 30% 13%) 60%, var(--panel))` : "var(--panel-2)" }} />
        <div className="hero-body">
          {p ? <ProfileAvatar name={p.display_name} hue={p.avatar_hue} size={96} radius={24} /> : <Skeleton h={96} w={96} />}
          <div className="who">
            {!p || !d ? <><Skeleton h={24} w={200} /><Skeleton h={14} w={140} /></> : (
              <>
                <h2 className="who-name">{p.display_name}
                  <span className={`badge ${p.is_public ? "info" : ""}`}><Icon name={p.is_public ? "eye" : "eyeoff"} size={12} />{p.is_public ? "Public" : "Private"}</span>
                  {d.account.totp_enabled && <span className="badge good"><Icon name="shield" size={12} />2FA</span>}
                </h2>
                <div className="handle">{p.handle ? `@${p.handle}` : "No handle yet"} · {d.account.email}</div>
                {p.bio ? <p className="bio">{p.bio}</p> : <p className="muted small">Add a bio so people know what you build.</p>}
                <div className="meta">
                  <span><Icon name="calendar" size={13} />Joined {monthYear(d.account.created_at)}</span>
                  <ProfileLinks p={p} />
                </div>
              </>
            )}
          </div>
          <div className="hero-actions">
            {publicUrl && <>
              <a className="btn secondary" href={p!.public_path!} target="_blank" rel="noreferrer"><Icon name="ext" size={14} />Public page</a>
              <span className="btn secondary icon" title="Copy public link"><CopyButton text={publicUrl} size={14} /></span>
            </>}
            <button className="btn" onClick={() => setEditing(true)} disabled={!p}><Icon name="edit" size={14} />Edit profile</button>
          </div>
        </div>
      </Card>

      <div className="grid g-stats">
        <Stat accent label="Tokens launched" icon="rocket" value={s ? s.launches.total : "–"}
          hint={s ? `${s.launches.live} live · ${s.launches.failed} failed${s.launches.imported ? ` · ${s.launches.imported} imported` : ""}` : undefined} />
        <Stat label="Trades" icon="trend" value={s ? s.trades.count : "–"}
          hint={s ? `${fmt(s.trades.volume_sol, 3)} SOL volume · ${s.trades.buys} buys, ${s.trades.sells} sells` : undefined} />
        <Stat label="Net flow · Solana" icon="coins" value={s ? `${s.net_sol_flow > 0 ? "+" : ""}${fmt(s.net_sol_flow)}` : "–"} unit={s ? "SOL" : undefined}
          tone={s && s.net_sol_flow > 0 ? "good" : s && s.net_sol_flow < 0 ? "bad" : undefined} hint="All time · Solana transactions only; other chains aren't summed yet" />
        <Stat label="Platform fees paid" icon="pie" value={s ? fmt(s.fees_paid_sol, 4) : "–"} unit={s ? "SOL" : undefined}
          hint={s ? `Solana trades only · ${s.wallets.managed} managed wallets, ${s.wallets.watch} watch-only` : undefined} />
      </div>

      <div className="grid g-main">
        <div className="col">
          <Card title="Activity" icon="activity"
            sub={d ? `${d.heatmap.total} action${d.heatmap.total === 1 ? "" : "s"} in the last 26 weeks · ${d.heatmap.active_days} active day${d.heatmap.active_days === 1 ? "" : "s"}` : undefined}
            actions={d && d.heatmap.streak > 0 ? <span className="badge warn"><Icon name="flame" size={12} />{d.heatmap.streak}-day streak</span> : undefined}
            foot={<>Every action on your account counts: sign-ins, wallet changes, launches, trades and settings. <Link to="/activity">Full log</Link></>}>
            {!d ? <Skeleton h={150} /> : <Heatmap start={d.heatmap.start} days={d.heatmap.days} />}
          </Card>

          <Card title="Launchpads" icon="layers" sub="Tokens you created, by launchpad" flush={!s || s.launchpads.length === 0}>
            {!s ? <Loading /> : s.launchpads.length === 0 ? (
              <Empty icon="rocket" title="No launches yet" text="Launch a token on any chain and it shows up here."
                action={<Link to="/launches" className="btn sm"><Icon name="plus" size={14} />New launch</Link>} />
            ) : <PadBars rows={s.launchpads} />}
          </Card>

          <Card title="Recent launches" icon="rocket" flush actions={<Link to="/launches" className="btn sm ghost">All<Icon name="arrowright" size={13} /></Link>}>
            {!d ? <Loading /> : d.recent_launches.length === 0 ? <Empty icon="rocket" title="Nothing launched yet" /> : (
              <TableWrap><table>
                <thead><tr><th>Token</th><th>Launchpad</th><th>Status</th><th className="hide-sm">Mint</th></tr></thead>
                <tbody>
                  {d.recent_launches.map((l) => (
                    <tr key={l.id}>
                      <td><Link to={`/launches/${l.id}`} style={{ color: "var(--text)", fontWeight: 600 }}>{l.name}</Link> <span className="muted">${l.symbol}</span>
                        {l.quote && <span className="badge violet" style={{ marginLeft: 6 }}>/{l.quote}</span>}</td>
                      <td><PadLabel pad={l.launchpad} network={l.network} size={18} showChain /></td>
                      <td><StatusBadge status={l.status} /></td>
                      <td className="hide-sm">{l.mint_address ? <Address address={l.mint_address} type="token" network={l.network} /> : <span className="muted">–</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table></TableWrap>
            )}
          </Card>
        </div>

        <div className="col">
          <Card title="Milestones" icon="star" actions={d && <span className="muted small num">{done}/{d.milestones.length}</span>}>
            {!d ? <Loading /> : (
              <div className="milestones">
                {d.milestones.map((m) => (
                  <div key={m.id} className={`milestone ${m.done ? "done" : ""}`}>
                    <span className="mi"><Icon name={m.done ? "check" : MILESTONE_ICON[m.id] ?? "star"} size={14} stroke={m.done ? 3 : 2} /></span>
                    <div className="grow" style={{ minWidth: 0 }}><b>{m.label}</b><span>{m.hint}</span></div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title="Account" icon="user" actions={<Link to="/settings" className="btn sm ghost">Settings<Icon name="arrowright" size={13} /></Link>}>
            {!d ? <Loading /> : (
              <div className="kv">
                <div className="k">Email</div><div className="v truncate">{d.account.email}</div>
                <div className="k">Member since</div><div className="v">{parseDate(d.account.created_at).toLocaleDateString(undefined, { dateStyle: "medium" })}</div>
                <div className="k">Last sign-in</div><div className="v">{d.account.last_sign_in ? relTime(d.account.last_sign_in) : "–"}</div>
                <div className="k">Two-factor</div><div className="v">
                  <span className={`badge ${d.account.totp_enabled ? "good" : "warn"}`}><span className="dot" />{d.account.totp_enabled ? "On" : "Off"}</span></div>
                <div className="k">Telegram</div><div className="v">
                  <span className={`badge ${d.account.telegram_linked ? "info" : ""}`}><span className="dot" />{d.account.telegram_linked ? "Linked" : "Not linked"}</span></div>
                <div className="k">Active sessions</div><div className="v num">{d.account.active_sessions}</div>
                <div className="k">Tracked addresses</div><div className="v num">{s?.tracked ?? 0}</div>
              </div>
            )}
          </Card>

          <Card title="Chains" icon="globe" sub="Where you launch and hold wallets" flush>
            {!s ? <Loading /> : s.networks.length === 0 ? <Empty icon="globe" title="No chains yet" text="Create wallets or a launch to get started." /> : (
              <div className="feed">
                {s.networks.map((n) => (
                  <div key={n.network} className="feed-item" style={{ alignItems: "center" }}>
                    <ChainMark network={n.network} size={26} />
                    <div className="grow"><div style={{ fontWeight: 550 }}>{chainOf(n.network).name}</div>
                      <div className="muted small">{n.launches} launch{n.launches === 1 ? "" : "es"} · {n.wallets} wallet{n.wallets === 1 ? "" : "s"}</div></div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <ReferralCard data={d?.referral ?? null} onChange={load} />
        </div>
      </div>

      {editing && p && (
        <EditProfile p={p} onClose={() => setEditing(false)}
          onSaved={(np) => { setEditing(false); setD((x) => (x ? { ...x, profile: np } : x)); load(); refresh(); }} />
      )}
    </>
  );
}
