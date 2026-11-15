import TestLaunch from "../components/TestLaunch";
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
  { icon: "shield", title: "Every move, your decision", copy: "See exactly what an action will do before you approve it. Keep the confirmations and receipts in one place.", detail: "Preview · confirm · follow through" },
];

export default function Landing() {
  const [params] = useSearchParams();
  const refCode = (params.get("ref") ?? "").toUpperCase();
  const { pads, padsLoaded, health } = usePublicInfo();
  const groups = padsByChain(pads.filter(p => p.status === "available"));
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
            <h1 id="hero-title">An idea is just<br />the beginning.<br /><span>Make it a launch.</span></h1>
            <p className="hero-description">From your first token to the wallets behind it. Create, launch and run your operation across chains — from one desk built for the whole journey.</p>
            <div className="pub-ctas"><Link to={register} className="btn lg">Start your next launch<Icon name="arrowright" size={17} /></Link><a href="#how-it-works" className="btn lg ghost">See how it works<Icon name="chevron" size={15} /></a></div>
            {refCode && <p className="small text-2">Your referral code <span className="mono">{refCode}</span> will be applied when you create an account.</p>}
            <SignerNote health={health} />
          </div>
          <TestLaunch />
        </div>
        <div className="pub-wrap hero-network-band"><span className="eyebrow">A connected workspace</span><ChainStrip pads={pads} counts={false} /></div>
      </section>
      <section className="pub-section" id="how-it-works" aria-labelledby="workflow-title"><div className="pub-wrap">
        <div className="sec-head"><span className="eyebrow">FROM IDEA TO OPERATION</span><h2 id="workflow-title">Everything around the launch,<br />in one place.</h2><p className="text-2">A clear path from the first draft to the next decision.</p></div>
        <div className="workflow-grid">{[
          ["01", "Prepare your launch", "Choose a supported chain and launchpad. Add your token details, developer wallet, and supply plan."],
          ["02", "Make it yours", "Configure the options your launchpad supports. Choose your wallet, check the details, and confirm when you are ready."],
          ["03", "Run your operation", "Follow confirmation in your workspace, manage holdings, claim rewards, and track what your wallets control."],
        ].map(([number, title, copy]) => <article key={number}><span className="workflow-number">{number}</span><h3>{title}</h3><p>{copy}</p></article>)}</div>
      </div></section>
      <section className="pub-section alt" id="capabilities" aria-labelledby="capabilities-title"><div className="pub-wrap"><div className="sec-head"><span className="eyebrow">BUILT FOR THE WORK AFTER LAUNCH</span><h2 id="capabilities-title">Stay in control of the details.</h2></div><div className="feature-grid">{FEATURES.map(feature => <article className="feature-tile" key={feature.title}><span className="feature-mark"><Icon name={feature.icon} size={23} /></span><h3>{feature.title}</h3><p>{feature.copy}</p><small>{feature.detail}</small></article>)}</div></div></section>
      <section className="pub-section alt" id="coverage" aria-labelledby="coverage-title"><div className="pub-wrap"><div className="sec-head"><span className="eyebrow">PICK YOUR LAUNCHPAD</span><h2 id="coverage-title">One interface. Different ecosystems.</h2><p className="text-2">Availability comes from this deployment. Each launchpad has its own supported settings, costs, and permissions.</p></div>
        {!padsLoaded ? <p className="row muted" role="status"><Spinner />Checking launchpad availability…</p> : !pads.length ? <p className="coverage-unavailable">Launchpad availability could not be loaded. Open the app to check again.</p> : <div className="coverage-grid">{groups.map(group => <section className="coverage-card" key={group.chain.id}><h3><ChainMark network={group.chain.id} size={22} />{group.chain.name}</h3><ul>{group.pads.map(pad => <li key={pad.id}><PadMark pad={pad.id} size={22} /><span>{padName(pad.id)}</span><span className={`badge ${pad.status === "available" ? "good" : ""}`} title={pad.reason}>{pad.status === "available" ? "Available" : pad.status === "planned" ? "Planned" : "Needs setup"}</span></li>)}</ul></section>)}</div>}
      </div></section>
      <section className="pub-section" id="safeguards" aria-labelledby="safeguards-title"><div className="pub-wrap sg-in"><div className="sec-head"><span className="eyebrow">CLEAR APPROVALS, VISIBLE OUTCOMES</span><h2 id="safeguards-title">Know what you are confirming.</h2><p className="text-2">A review comes before the signature. Transaction history stays available after it.</p></div><SafeguardList /></div></section>
      <section className="pub-cta"><div className="pub-wrap"><div className="pub-cta-in"><div><span className="eyebrow">YOUR NEXT CHAPTER STARTS HERE</span><h2>Set up your launch desk.</h2><p className="text-2">Create an account, organize your wallets, and prepare your first draft.</p></div><Link to={register} className="btn lg">Get started<Icon name="arrowright" size={17} /></Link></div></div></section>
    </main>
    <footer className="pub-footer"><div className="pub-wrap pub-footer-in"><Brand compact /><p className="muted small">{RISK_NOTE}</p></div></footer>
  </div>;
}
