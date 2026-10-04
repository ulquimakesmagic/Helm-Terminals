import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CHAINS, padName, padsByChain } from "../chains";
import { ChainMark, PadLabel, PadMark } from "../components/marks";
import { Brand, ChainStrip, PublicHeader, RISK_NOTE, SafeguardList, SignerNote, usePublicInfo } from "../components/public";
import { ReviewHero, summarize } from "../components/review";
import { Icon, IconName, Spinner } from "../components/ui";

/*
 * The public front door at "/" for signed-out visitors. Every claim is something the product does today; counts
 * and availability come from the live launchpad list. The example review uses the same component and summary
 * logic as the real confirmation dialog, with made-up values that are labelled as an example.
 */

const EXAMPLE_PREVIEW = {
  kind: "deploy", launchpad: "pumpfun", network: "solana", name: "Example Token", symbol: "EXMPL",
  dev_wallet: "ExampLeDevWa11etXXXXXXXXXXXXXXXXXXXXXXXX1111", dev_buy_sol: 0.25, platform_fee: "none on deploy",
};

const STATUS: Record<string, [string, string]> = {
  available: ["Live", "live"], setup_required: ["Needs setup", "setup"],
};

function Capability({ icon, title, children, spec }: { icon: IconName; title: string; children: React.ReactNode; spec: string[] }) {
  return (
    <article className="cap">
      <span className="cap-ic" aria-hidden="true"><Icon name={icon} size={17} /></span>
      <h3 className="cap-title">{title}</h3>
      <p>{children}</p>
      <ul className="cap-spec">{spec.map((s) => <li key={s}>{s}</li>)}</ul>
    </article>
  );
}

export default function Landing() {
  const [params] = useSearchParams();
  const refCode = (params.get("ref") ?? "").toUpperCase();
  const { pads, padsLoaded, health } = usePublicInfo();
  const groups = padsByChain(pads);
  const live = pads.filter((p) => p.status === "available").length;
  const example = summarize(EXAMPLE_PREVIEW, "solana")!;
  const refQ = refCode ? `&ref=${encodeURIComponent(refCode)}` : "";
  useEffect(() => { document.title = "Helm Terminals · Multichain launch desk"; }, []);

  return (
    <div className="pub">
      <a href="#pub-main" className="skip-link">Skip to content</a>
      <PublicHeader refCode={refCode} />

      <main id="pub-main">
        <section className="pub-hero">
          <div className="pub-wrap pub-hero-in">
            <div className="pub-hero-copy">
              <span className="eyebrow">Multichain launch desk</span>
              <h1>Launch and run tokens across chains, <span className="grad-text">with every action reviewed first.</span></h1>
              <p className="lead">
                Create tokens on the launchpads Helm supports, manage the wallets behind them, and check every wallet,
                amount and fee before anything is signed.
              </p>
              <div className="pub-ctas">
                <Link to={`/login?mode=register${refQ}`} className="btn lg">Create account<Icon name="arrowright" size={15} /></Link>
                <Link to={`/login?mode=login${refQ}`} className="btn lg secondary">Sign in</Link>
              </div>
              {refCode && <p className="small text-2"><Icon name="gift" size={13} /> Referral code <span className="mono">{refCode}</span> will be applied when you create your account.</p>}
              <ChainStrip pads={pads} />
              <p className="small muted">
                {padsLoaded && pads.length
                  ? <>{pads.length} launchpads listed across {groups.length} chains · {live} live now</>
                  : padsLoaded ? "Launchpad list unavailable right now." : "Loading launchpad list…"}
              </p>
            </div>

            <figure className="pub-demo" aria-label="Example of Helm's review step">
              <div className="demo-bar">
                <span className="demo-dots" aria-hidden="true"><i /><i /><i /></span>
                <span className="mono small">Deploy Example Token</span>
                <span className="demo-tag">Example · not a live order</span>
              </div>
              <div className="demo-body">
                <div className="steps" aria-hidden="true">
                  <span className="s done"><span className="n"><Icon name="check" size={11} stroke={3} /></span>Preview</span>
                  <span className="sep" />
                  <span className="s on"><span className="n">2</span>Confirm</span>
                  <span className="sep" />
                  <span className="s"><span className="n">3</span>Result</span>
                </div>
                <ReviewHero s={example} />
                <div className="demo-route">
                  <PadLabel pad="pumpfun" network="solana" size={18} showChain />
                </div>
                <ol className="next-steps">{example.next.map((n) => <li key={n}>{n}</li>)}</ol>
                <p className="muted small demo-note">
                  <Icon name="eye" size={13} />Nothing is sent until you confirm. The confirmation is tied to this exact preview.
                </p>
              </div>
              <figcaption className="sr-only">An example deployment review as it appears in Helm, with made-up values.</figcaption>
            </figure>
          </div>
        </section>

        <section className="pub-section" id="coverage" aria-labelledby="cov-h">
          <div className="pub-wrap">
            <div className="sec-head">
              <span className="eyebrow">Coverage</span>
              <h2 id="cov-h">Chains and launchpads, straight from the product</h2>
              <p className="text-2">This list is read live from Helm. <b>Live</b> launchpads can be used now; <b>Needs setup</b> ones aren't switched on for this deployment yet; <b>Soon</b> ones are planned.</p>
            </div>
            {!padsLoaded ? (
              <div className="row muted" style={{ gap: 8 }}><Spinner />Loading launchpads…</div>
            ) : !groups.length ? (
              <p className="muted">The launchpad list couldn't be loaded right now. Supported chains: {Object.values(CHAINS).map((c) => c.name).join(", ")}.</p>
            ) : (
              <div className="cov-grid">
                {groups.map(({ chain, pads: list }) => (
                  <section key={chain.id} className="cov-chain" aria-label={chain.name}>
                    <header>
                      <ChainMark network={chain.id} size={22} />
                      <b>{chain.name}</b>
                      <span className="muted small">{list.filter((p) => p.status === "available").length} of {list.length} live</span>
                    </header>
                    <ul>
                      {list.map((p) => {
                        const [label, cls] = STATUS[p.status] ?? ["Soon", "soon"];
                        return (
                          <li key={p.id}>
                            <PadMark pad={p.id} size={22} />
                            <span className="grow">{padName(p.id)}</span>
                            <span className={`cov-st ${cls}`}><span className="dot" aria-hidden="true" />{label}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="pub-section alt" id="capabilities" aria-labelledby="cap-h">
          <div className="pub-wrap">
            <div className="sec-head">
              <span className="eyebrow">What it does</span>
              <h2 id="cap-h">One desk for the whole launch</h2>
            </div>
            <div className="cap-grid">
              <Capability icon="rocket" title="Launch workflows" spec={[`${pads.length || "Several"} launchpads, ${groups.length || 5} chains`, "Drafts, deploy review, token workspace"]}>
                Pick a chain and launchpad, fill in the token, and deploy from a dev wallet you manage. Import a token you
                already hold to trade and manage it the same way.
              </Capability>
              <Capability icon="wallet" title="Wallets and supply" spec={["Managed and watch-only wallets", "Disperse, consolidate, route USDC"]}>
                Create wallets on every supported chain, group them in folders, and split or combine balances. Each token
                workspace shows how much of the supply your wallets hold next to the largest holders.
              </Capability>
              <Capability icon="eye" title="Review before you confirm" spec={["Wallets, amounts, fees, gas", "Confirmation tied to the preview"]}>
                Deploys, trades, transfers and routes all start as a preview. You see the exact wallets, amounts and fees,
                then confirm that preview, not a new one.
              </Capability>
              <Capability icon="calendar" title="Finite scheduled plans" spec={["2 to 12 orders per plan", "At least 5 minutes apart", "Buys or sells, never both", "Budget caps per chain"]}>
                Schedule a set number of buys, or staged sells of a share of your holdings, on a live launch. Each plan
                has visible limits and stops when its orders are done.
              </Capability>
              <Capability icon="activity" title="Activity and portfolio" spec={["Balances valued in USD", "Unread chains are marked, not zeroed"]}>
                See coins and dollar stablecoins across every chain in one place, and an audit log of launches, trades,
                transfers and sign-ins.
              </Capability>
              <Capability icon="telegram" title="Telegram status" spec={["Link from Settings", "Read-only account access"]}>
                Check your launches and wallets from Telegram once you link your account. Review and confirm
                financial actions in the Helm web app.
              </Capability>
            </div>
          </div>
        </section>

        <section className="pub-section" id="safeguards" aria-labelledby="sg-h">
          <div className="pub-wrap sg-in">
            <div className="sec-head">
              <span className="eyebrow">Safeguards</span>
              <h2 id="sg-h">What Helm checks before funds move</h2>
              <SignerNote health={health} />
            </div>
            <SafeguardList />
          </div>
        </section>

        <section className="pub-cta" aria-label="Get started">
          <div className="pub-wrap"><div className="pub-cta-in">
            <div>
              <h2>Set up your desk</h2>
              <p className="text-2">An email and password is all it takes. You can add two-factor authentication from Settings.</p>
            </div>
            <div className="pub-ctas">
              <Link to={`/login?mode=register${refQ}`} className="btn lg">Create account<Icon name="arrowright" size={15} /></Link>
              <Link to={`/login?mode=login${refQ}`} className="btn lg secondary">Sign in</Link>
            </div>
          </div></div>
        </section>
      </main>

      <footer className="pub-footer">
        <div className="pub-wrap pub-footer-in">
          <Brand compact />
          <p className="muted small">{RISK_NOTE}</p>
        </div>
      </footer>
    </div>
  );
}
