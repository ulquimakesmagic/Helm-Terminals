import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, errMsg, hasToken } from "../api";
import { chainOf } from "../chains";
import { PadLabel } from "../components/marks";
import { PadBars, PadRow, ProfileAvatar } from "../components/profile";
import { Address, Callout, Card, Empty, Icon, Loading, Logo, Stat, StatusBadge, parseDate } from "../components/ui";
import { ProfileLinks } from "./Profile";

interface PublicData {
  display_name: string; handle: string; bio: string; x_handle: string; telegram_handle: string; website: string;
  avatar_hue: number; joined: string | null;
  stats: { deployed: number; live: number; networks: string[]; launchpads: PadRow[] };
  launches: { name: string; symbol: string; network: string; launchpad: string; status: string; mint_address: string; quote: string | null; deployed_at: string | null }[];
}

export default function PublicProfile() {
  const { handle = "" } = useParams();
  const [d, setD] = useState<PublicData | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setD(null); setMissing(false); setError("");
    api<PublicData>(`/u/${encodeURIComponent(handle)}`)
      .then((x) => { setD(x); document.title = `${x.display_name} (@${x.handle}) · Helm Terminals`; })
      .catch((e) => ((e as ApiError).status === 404 ? setMissing(true) : setError(errMsg(e))));
  }, [handle]);

  const joined = d?.joined ? new Date(d.joined + "-01T00:00:00Z").toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" }) : null;

  return (
    <div className="public-page">
      <header className="public-top">
        <Link to="/" className="row nowrap" style={{ gap: 10, color: "var(--text)" }}>
          <Logo size={28} /><b>Helm Terminals</b>
        </Link>
        <div className="spacer" style={{ flex: 1 }} />
        <Link to={hasToken() ? "/" : "/login"} className="btn sm secondary">{hasToken() ? "Open app" : "Sign in"}</Link>
      </header>

      <main className="public-main">
        {error && <Callout tone="err">{error}</Callout>}
        {missing ? (
          <Card><Empty icon="user" title="Profile not found" text="This creator page doesn't exist or isn't public." /></Card>
        ) : !d ? <Card><Loading rows={4} /></Card> : (
          <>
            <Card className="profile-hero" flush>
              <div className="banner" style={{ background: `linear-gradient(120deg, hsl(${d.avatar_hue} 35% 22%), hsl(${(d.avatar_hue + 50) % 360} 30% 13%) 60%, var(--panel))` }} />
              <div className="hero-body">
                <ProfileAvatar name={d.display_name} hue={d.avatar_hue} size={96} radius={24} />
                <div className="who">
                  <h1>{d.display_name}</h1>
                  <div className="handle">@{d.handle}</div>
                  {d.bio && <p className="bio">{d.bio}</p>}
                  <div className="meta">
                    {joined && <span><Icon name="calendar" size={13} />Joined {joined}</span>}
                    <ProfileLinks p={d} />
                  </div>
                </div>
              </div>
            </Card>

            <div className="grid g-stats">
              <Stat accent label="Tokens deployed" icon="rocket" value={d.stats.deployed} hint="Through Helm Terminals" />
              <Stat label="Live now" icon="zap" value={d.stats.live} />
              <Stat label="Chains" icon="globe" value={d.stats.networks.length}
                hint={d.stats.networks.map((n) => chainOf(n).name).join(", ") || "–"} />
            </div>

            {d.stats.launchpads.length > 0 && (
              <Card title="Launchpads" icon="layers"><PadBars rows={d.stats.launchpads} /></Card>
            )}

            <Card title="Launch history" icon="rocket" sub="Every token this creator deployed through Helm, newest first. Check any of them on the chain's explorer." flush>
              {d.launches.length === 0 ? <Empty icon="rocket" title="No tokens deployed yet" /> : (
                <div className="table-wrap"><table>
                  <thead><tr><th>Token</th><th>Launchpad</th><th>Status</th><th>Mint</th><th className="hide-sm">Deployed</th></tr></thead>
                  <tbody>
                    {d.launches.map((l) => (
                      <tr key={l.mint_address}>
                        <td><b>{l.name}</b> <span className="muted">${l.symbol}</span>
                          {l.quote && <span className="badge violet" style={{ marginLeft: 6 }}>/{l.quote}</span>}</td>
                        <td><PadLabel pad={l.launchpad} network={l.network} size={18} showChain /></td>
                        <td><StatusBadge status={l.status} /></td>
                        <td><Address address={l.mint_address} type="token" network={l.network} /></td>
                        <td className="hide-sm muted">{l.deployed_at ? parseDate(l.deployed_at).toLocaleDateString(undefined, { dateStyle: "medium" }) : "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
              )}
            </Card>

            <p className="muted small" style={{ textAlign: "center" }}>
              Helm Terminals shows creators' launch history in the open. It doesn't show wallets, balances or profits.
            </p>
          </>
        )}
      </main>
    </div>
  );
}
