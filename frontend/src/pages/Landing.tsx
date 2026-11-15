import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { padName, padsByChain } from "../chains";
import { ChainMark, PadMark } from "../components/marks";
import { Brand, ChainStrip, PublicHeader, RISK_NOTE, SafeguardList, SignerNote, usePublicInfo } from "../components/public";
import { Icon, IconName, Spinner } from "../components/ui";
import { setMeta } from "../meta";

const FEATURES: { icon: IconName; title: string; copy: string; detail: string }[] = [
  { icon: "rocket", title: "A launch desk that stays with you", copy: "Prepare a token, choose a launchpad, and review its costs. Come back to the same workspace to operate it after launch.", detail: "Draft · review · deploy · manage" },
  { icon: "wallet", title: "Your wallets, working together", copy: "Organize wallets into folders, move balances, and see the supply you control together in one view.", detail: "Funding · transfers · swaps · supply" },
  { icon: "trend", title: "Keep the whole picture in view", copy: "Track balances, inspect market data, and follow the outcome of each action. Missing data stays clearly marked.", detail: "Portfolio · activity · wallet tracker" },
  { icon: "calendar", title: "Plans with a clear finish", copy: "Schedule a bounded buy or staged exit from one selected wallet on supported chains. Set the limits and stop the plan when you need to.", detail: "Fixed budget · limited orders · pause or cancel" },
];

export default function Landing() {
  const [params] = useSearchParams();
  const refCode = (params.get("ref") ?? "").toUpperCase();
  const { pads, padsLoaded, health } = usePublicInfo();
  const groups = padsByChain(pads);
  const register = `/login?mode=register${refCode ? `&ref=${encodeURIComponent(refCode)}` : ""}`;
  useEffect(() => { setMeta({ title: "Helm Terminals | Your launch. Your rewards.", index: true }); }, []);
  return <div className="pub landing-v2">
    <a href="#pub-main" className="skip-link">Skip to content</a>
    <PublicHeader refCode={refCode} />
    <main id="pub-main">
      <section className="launch-hero" aria-labelledby="hero-title">
        <div className="pub-wrap launch-hero-grid">
          <div className="launch-hero-copy">
            <div className="hero-kicker"><span className="hero-rule" />ONE DESK. EVERY STAGE.</div>
            <h1 id="hero-title">Your next launch.<br /><span>Your whole operation.</span></h1>
            <p className="hero-description">Launch tokens across supported chains with no Helm launch fee. Manage your wallets, trade with clear costs, and keep 97.5% of creator rewards from new Helm launches.</p>
            <div className="pub-ctas"><Link to={register} className="btn lg">Open your launch desk<Icon name="arrowright" size={17} /></Link><a href="#how-it-works" className="btn lg ghost">See how it works<Icon name="chevron" size={15} /></a></div>
            <p className="hero-price-note"><b>Free launches</b><span>·</span><b>2.5% per trade</b><span>·</span>2.5% of creator rewards</p>
            {refCode && <p className="small text-2">Your referral code <span className="mono">{refCode}</span> will be applied when you create an account.</p>}
            <SignerNote health={health} />
          </div>
          <div className="terminal-preview" aria-label="Illustration of the Helm launch workflow. Example values only.">
            <div className="terminal-toolbar"><span className="terminal-brand"><Icon name="layers" size={16} />HELM / LAUNCHDESK</span><span className="badge">EXAMPLE</span></div>
            <div className="terminal-token"><span className="example-token-mark">E</span><div><strong>Example Token <span>EXMPL</span></strong><p>Solana <span> / </span>Pump.fun</p></div><span className="badge good">Ready to review</span></div>
            <div className="terminal-flow">{["Prepare", "Review", "Launch", "Manage"].map((step, index) => <div key={step} className={index === 1 ? "current" : ""}><span>{String(index + 1).padStart(2, "0")}</span>{step}</div>)}</div>
            <div className="terminal-summary"><span className="eyebrow">YOUR LAUNCH, BEFORE YOU SIGN</span><div><span>Helm launch fee</span><b>Free</b></div><div><span>Your creator rewards</span><b className="good">97.5% yours</b></div><div><span>Trading on Helm</span><b>2.5% per trade</b></div><div><span>Network & launchpad costs</span><b>Shown in review</b></div></div>
            <div className="terminal-preview-foot"><Icon name="eye" size={16} /><span>Review the wallets, amounts, and fees.<br /><b>You decide when to confirm.</b></span><Icon name="arrowright" size={17} /></div>
          </div>
        </div>
        <div className="pub-wrap hero-network-band"><span className="eyebrow">A connected workspace</span><ChainStrip pads={pads} counts={false} /></div>
      </section>
      <section className="pub-section" id="how-it-works" aria-labelledby="workflow-title"><div className="pub-wrap">
        <div className="sec-head"><span className="eyebrow">FROM IDEA TO OPERATION</span><h2 id="workflow-title">Everything around the launch,<br />in one place.</h2><p className="text-2">A clear path from the first draft to the next decision.</p></div>
        <div className="workflow-grid">{[
          ["01", "Prepare your launch", "Choose a supported chain and launchpad. Add your token details, developer wallet, and supply plan."],
          ["02", "Review every cost", "Review network and launchpad costs, funding, recipients, and any initial-buy trading fee before confirming. Helm charges nothing to create a launch."],
          ["03", "Run your operation", "Follow confirmation in your workspace, manage holdings, claim rewards, and track what your wallets control."],
        ].map(([number, title, copy]) => <article key={number}><span className="workflow-number">{number}</span><h3>{title}</h3><p>{copy}</p></article>)}</div>
      </div></section>
      <section className="pub-section alt" id="capabilities" aria-labelledby="capabilities-title"><div className="pub-wrap"><div className="sec-head"><span className="eyebrow">BUILT FOR THE WORK AFTER LAUNCH</span><h2 id="capabilities-title">Stay in control of the details.</h2></div><div className="feature-grid">{FEATURES.map(feature => <article className="feature-tile" key={feature.title}><span className="feature-mark"><Icon name={feature.icon} size={23} /></span><h3>{feature.title}</h3><p>{feature.copy}</p><small>{feature.detail}</small></article>)}</div></div></section>
      <section className="pub-section" id="pricing" aria-labelledby="pricing-title"><div className="pub-wrap"><div className="sec-head"><span className="eyebrow">PRICING YOU CAN READ IN A SENTENCE</span><h2 id="pricing-title">Free to launch. Clear fees as you grow.</h2><p className="text-2">Launch for free. Helm earns 2.5% on trades and 2.5% of creator rewards from tokens launched here.</p></div><div className="pricing-grid">
        <article><span>Per launch</span><strong>Free<span> / launch</span></strong><p>No Helm launch fee, for every account. Create your launch and keep your creator rewards.</p><small>Network gas and third-party launchpad costs still apply. Optional Pro infrastructure is planned for later.</small></article>
        <article className="pricing-featured"><span>Per trade</span><strong>2.5%<span> / trade</span></strong><p>A disclosed fee on trades carried out through Helm. The same trading rate applies to admin accounts.</p><small>Network gas and launchpad fees are separate and shown before confirmation.</small></article>
        <article><span>Creator rewards you keep</span><strong>97.5%<span> / yours</span></strong><p>Helm receives 2.5% of creator rewards on new Helm launches. Collection availability is shown for each launchpad.</p><small>Existing immutable reward splits retain their original on-chain terms.</small></article>
      </div><div className="referral-strip"><Icon name="gift" size={21} /><div><b>Bring your community. Earn 30% of the fees they pay.</b><p>A $100 trade pays a $2.50 Helm fee and earns its referrer $0.75. Free launches generate no referral fee.</p></div><Link to={register} className="btn secondary">Start referring<Icon name="arrowright" size={15} /></Link></div></div></section>
      <section className="pub-section alt" id="coverage" aria-labelledby="coverage-title"><div className="pub-wrap"><div className="sec-head"><span className="eyebrow">PICK YOUR LAUNCHPAD</span><h2 id="coverage-title">One interface. Different ecosystems.</h2><p className="text-2">Availability comes from this deployment. Each launchpad has its own supported settings, costs, and permissions.</p></div>
        {!padsLoaded ? <p className="row muted" role="status"><Spinner />Checking launchpad availability…</p> : !pads.length ? <p className="coverage-unavailable">Launchpad availability could not be loaded. Open the app to check again.</p> : <div className="coverage-grid">{groups.map(group => <section className="coverage-card" key={group.chain.id}><h3><ChainMark network={group.chain.id} size={22} />{group.chain.name}</h3><ul>{group.pads.map(pad => <li key={pad.id}><PadMark pad={pad.id} size={22} /><span>{padName(pad.id)}</span><span className={`badge ${pad.status === "available" ? "good" : ""}`} title={pad.reason}>{pad.status === "available" ? "Available" : pad.status === "planned" ? "Planned" : "Needs setup"}</span></li>)}</ul></section>)}</div>}
      </div></section>
      <section className="pub-section" id="safeguards" aria-labelledby="safeguards-title"><div className="pub-wrap sg-in"><div className="sec-head"><span className="eyebrow">CLEAR APPROVALS, VISIBLE OUTCOMES</span><h2 id="safeguards-title">Know what you are confirming.</h2><p className="text-2">A review comes before the signature. Transaction history stays available after it.</p></div><SafeguardList /></div></section>
      <section className="pub-cta"><div className="pub-wrap"><div className="pub-cta-in"><div><span className="eyebrow">YOUR NEXT CHAPTER STARTS HERE</span><h2>Set up your launch desk.</h2><p className="text-2">Create an account, organize your wallets, and prepare your first draft.</p></div><Link to={register} className="btn lg">Get started<Icon name="arrowright" size={17} /></Link></div></div></section>
    </main>
    <footer className="pub-footer"><div className="pub-wrap pub-footer-in"><Brand compact /><p className="muted small">{RISK_NOTE}</p></div></footer>
  </div>;
}
