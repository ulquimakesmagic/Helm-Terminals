import { ReactNode, useEffect } from "react";
import { Link } from "react-router-dom";
import { PublicFooter, PublicHeader } from "../components/public";
import { Callout } from "../components/ui";
import { setMeta } from "../meta";

/*
 * Drafts of Helm's Terms of Service, Privacy Policy and Risk Disclosure. They describe the product as built: managed
 * wallets signed through Turnkey, connected wallets signed by the user, fees shown in every preview, Codex market
 * data, no tracking. Anything only the operator can supply (legal entity, address, governing law, support contact,
 * retention periods) is marked as a placeholder and must be completed after legal review. Nothing here is legal advice.
 */

export const LEGAL_UPDATED = "6 October 2026";

function Pending({ children }: { children: ReactNode }) {
  return <mark className="legal-pending" title="To be completed by the operator after legal review">[{children}]</mark>;
}

const OPERATOR = <Pending>Operator legal name, registered address and company number</Pending>;
const CONTACT = <Pending>Support and privacy contact address</Pending>;

type Doc = { title: string; description: string; path: string; intro: ReactNode; sections: [string, ReactNode][] };

const TERMS: Doc = {
  title: "Terms of Service", path: "/terms",
  description: "The terms for using Helm Terminals, a multichain token launch and wallet operations desk.",
  intro: <>These terms govern your use of Helm Terminals ("Helm"), operated by {OPERATOR}. By creating an account or using Helm you agree to them. If you do not agree, do not use Helm.</>,
  sections: [
    ["What Helm is", <>
      <p>Helm is software for creating tokens on third-party launchpads, managing the blockchain wallets you use for them, and operating those tokens after launch: trades, transfers, fee claims, burns, routes between chains and reporting. Helm is not an exchange, broker, custodian of record, investment adviser or launchpad. Launchpads, routers, bridges and blockchains are operated by third parties.</p>
      <p>Features vary by chain and launchpad. The app shows which are available, need setup, are planned or unsupported; an unavailable feature is not a promise that it will be offered.</p>
    </>],
    ["Eligibility and your account", <>
      <p>You must be old enough to form a binding contract where you live and must not be barred from using services like Helm by sanctions or local law. Some assets and launchpads carry their own eligibility rules (for example, tokenized-stock pairs); you are responsible for meeting them.</p>
      <p>Keep your password and authenticator safe. Withdrawals and token sends require two-factor authentication. You are responsible for activity under your account until you tell us it is compromised. <Pending>Jurisdictions where Helm is not offered</Pending></p>
    </>],
    ["Wallets and keys", <>
      <p><b>Managed wallets.</b> Keys for wallets Helm creates or imports are held by a third-party key-management service (Turnkey) under Helm's organisation. Helm stores each key's reference and public address and asks the service to sign only when you confirm an action, or when a finite scheduled plan you created places one of its orders. Helm does not show or export private keys. Keep enough of your own records to identify your wallets and assets.</p>
      <p><b>Connected wallets</b> (Arc wallets you link) are signed in your own wallet app; Helm never holds their keys. <b>Watch-only</b> addresses are read, never signed for.</p>
      <p>Account recovery is limited to the methods the app offers (verified-email reset and recovery codes). After a recovery, spending is held for 24 hours. <Pending>Operator procedure if both password and email access are lost</Pending></p>
    </>],
    ["Previews, confirmations and transactions", <>
      <p>Every action that moves funds shows a preview: the network, wallets, amounts, fees, slippage and limits. Nothing is sent until you confirm. Some actions (swaps, routes) are bound to the minimum output shown; if a fresh quote would deliver less, Helm asks you to review again. Market orders still execute at the price available on chain within the slippage you set.</p>
      <p>Blockchain transactions are generally irreversible. A transaction can be sent but not confirmed in time; Helm then marks it unconfirmed and does not retry it automatically. Check the explorer before repeating an action.</p>
    </>],
    ["Fees", <>
      <p>Helm's fees are shown in each preview before you confirm. They can include a flat fee per launch ($10 by default, paid in the launch chain's coin at the live price once the launch is live, never more than the maximum coin amount shown in the preview; a failed launch pays nothing), a percentage of trades placed through Helm (1% by default, on Solana launchpads and on EVM trades routed through a DEX aggregator), a fee on cross-chain routes, and a share of creator rewards on launchpads that support splitting them (3% by default, or a fixed 6% on Pons where its splitter is enabled). Network fees, launchpad fees and third-party router or bridge fees are charged by those parties. Fees can change; the rate that applies is the one shown in the preview you confirm.</p>
    </>],
    ["Acceptable use", <>
      <p>You must not use Helm to break the law, evade sanctions, launder money, manipulate markets, create artificial trading volume, misrepresent token ownership or distribution, defraud buyers, infringe others' rights in token names or images, or interfere with Helm or third-party services. Helm deliberately excludes wash-trading and holder-disguising features. We may suspend accounts that breach these terms.</p>
    </>],
    ["Third-party services and data", <>
      <p>Helm depends on third parties including Turnkey (signing), launchpads such as Pump.fun, Bags, Meteora (via Jupiter), Flap, Four.meme, Clanker, Pons and Tolly, routers and bridges such as Jupiter, KyberSwap, Relay and deBridge, chain RPC providers, Codex for market data, and GMGN for embedded charts. Their outages, changes or errors can affect what Helm can do. Market data is indicative and is never used to price your transactions.</p>
    </>],
    ["Suspension and termination", <>
      <p>You may stop using Helm at any time. We may suspend or end access for breach, legal requirement or security risk. Suspension does not move your assets. <Pending>Process for users to move assets out of managed wallets after closure</Pending></p>
    </>],
    ["Disclaimers and liability", <>
      <p>Helm is provided "as is". To the extent the law allows, we disclaim warranties of uninterrupted or error-free operation and are not liable for losses from market movements, third-party services, blockchain behaviour, your instructions, or unauthorised access to your account that was not caused by our breach. <Pending>Liability cap, governing law, venue and dispute resolution</Pending></p>
    </>],
    ["Changes and contact", <>
      <p>We may update these terms; material changes will be shown in the app before they apply. Contact: {CONTACT}.</p>
    </>],
  ],
};

const PRIVACY: Doc = {
  title: "Privacy Policy", path: "/privacy",
  description: "What personal data Helm Terminals collects, why, who processes it, and the choices you have.",
  intro: <>This policy explains what {OPERATOR} ("we") collects when you use Helm Terminals and why. Contact: {CONTACT}.</>,
  sections: [
    ["What we collect", <ul>
      <li><b>Account:</b> email address, a hash of your password, your encrypted two-factor secret, one-time recovery codes (hashed), email verification and recovery records.</li>
      <li><b>Sessions and security:</b> session records with IP address and browser user agent, failed sign-in and code attempts (keyed to email or IP address), an activity log of sign-ins, launches, trades and transfers, and an administrative audit log.</li>
      <li><b>Wallets and launches:</b> public wallet addresses, key references from our signing provider, folder names and labels, launch drafts including token names, descriptions, images and social links, transactions Helm sent, fees recorded, and referral relationships.</li>
      <li><b>Optional:</b> a public profile (display name, handle, bio, links) if you publish one; your Telegram user ID if you link Telegram.</li>
    </ul>],
    ["Why we use it", <ul>
      <li>To provide the service: sign in, create and operate wallets, build, sign and track transactions, show balances and history.</li>
      <li>To protect accounts and funds: rate limits, two-factor checks, recovery holds, fraud and abuse prevention.</li>
      <li>To keep financial records of fees and referral shares.</li>
      <li>To meet legal obligations. <Pending>Lawful bases per purpose and any regulatory record-keeping duties</Pending></li>
    </ul>],
    ["Public blockchains", <p>Transactions Helm sends are recorded on public blockchains and can be linked to your wallet addresses by anyone. We cannot delete or alter on-chain data.</p>],
    ["Who processes data for us", <><p>We share only what each service needs:</p><ul>
      <li>Turnkey (key management and signing): wallet key references and the transactions to sign.</li>
      <li>Blockchain RPC providers and indexers for each chain: addresses and transactions to read or broadcast.</li>
      <li>Launchpads, routers and bridges (for example PumpPortal/Pump.fun, Bags, Jupiter, KyberSwap, Relay, deBridge, Flap, Four.meme): wallet addresses, amounts and token metadata needed to build your transactions.</li>
      <li>Codex: token and pool addresses for market data. No account data is sent.</li>
      <li>GMGN: on Solana, Ethereum and BNB Chain, token charts are GMGN's own page embedded in Helm. Your browser loads it directly from GMGN, which receives the token address and, like any website you visit, your IP address and browser details, and applies its own privacy policy. Helm sends GMGN no account data.</li>
      <li>Our hosting and database provider (Railway) and our email provider, if account recovery email is enabled.</li>
      <li>Telegram, if you link it.</li>
    </ul><p><Pending>Processor agreements, international transfer safeguards and data locations</Pending></p></>],
    ["Cookies and browser storage", <p>Helm does not use advertising or analytics cookies or trackers. The embedded GMGN chart is GMGN's page and may use its own storage under GMGN's policies. Your sign-in token is kept in the page's memory only and disappears when you reload or close the tab. Helm stores nothing else in your browser, so it shows no cookie banner. <Pending>Confirm this assessment for each market you serve</Pending></p>],
    ["Retention", <p>We keep account, wallet and transaction records while your account is open and afterwards as long as needed for security, accounting and legal purposes. Failed sign-in and code attempt records are pruned after about an hour. <Pending>Specific retention periods</Pending></p>],
    ["Your rights", <p>Depending on where you live, you may have rights to access, correct, delete, restrict or port your data and to object to processing. The app does not yet provide self-service export or deletion; contact {CONTACT} and we will respond within the time the law requires. Deleting an account cannot erase on-chain records, and we may need to keep some records for legal reasons. You can complain to your data protection authority.</p>],
    ["Security", <p>Passwords are hashed with Argon2, two-factor secrets are encrypted, session tokens are stored only as hashes, private keys are not stored in our database in production, and sensitive actions require recent re-authentication. No system is perfectly secure.</p>],
    ["Children", <p>Helm is not for children and we do not knowingly collect their data.</p>],
    ["Changes", <p>We will post changes here and highlight material changes in the app.</p>],
  ],
};

const RISK: Doc = {
  title: "Risk Disclosure", path: "/risk",
  description: "The main risks of launching and trading tokens with Helm Terminals.",
  intro: <>Launching and trading tokens is high risk. You can lose everything you put in. Read these risks before using Helm.</>,
  sections: [
    ["Market risk", <p>New tokens are extremely volatile and often illiquid. Prices can fall to zero within minutes. Market data in Helm comes from Codex and charts from GMGN or Codex; both are indicative, can be delayed or missing, and are never a promise of the price you will get.</p>],
    ["Execution risk", <p>Trades execute on chain at the price available when they land, within the slippage you set. Routes between chains depend on third-party solvers and can be delayed or refunded. A transaction can be sent and remain unconfirmed; repeating it can buy or send twice.</p>],
    ["Launchpad and protocol risk", <p>Launchpads, bonding curves, DEXes, routers and bridges are third-party smart contracts and services. They can change rules or fees, pause, fail, be exploited or reject your transaction. Creator rewards depend on each launchpad's own mechanics and may not accrue or be claimable.</p>],
    ["Custody and account risk", <p>Managed wallets are signed by a key-management service on Helm's instruction. Loss of your account access, a compromised session, an outage at Helm or its signing provider, or an operational error could delay or prevent access to funds. Two-factor authentication reduces, but does not remove, these risks.</p>],
    ["Technology risk", <p>Software can have bugs. Helm checks transactions built by third parties before signing (for example, it bounds how much a provider-built transaction may spend), but no check can prove a transaction is safe.</p>],
    ["Legal and tax risk", <p>Token launches, trading and fee arrangements may be regulated or taxed where you live. Rules change. You are responsible for your own compliance and taxes. Nothing in Helm is investment, legal or tax advice.</p>],
    ["No guarantees", <p>Helm does not promise any price, liquidity, holder count, listing or successful launch, and does not provide services designed to create artificial demand.</p>],
  ],
};

export const LEGAL = { terms: TERMS, privacy: PRIVACY, risk: RISK };

export default function Legal({ doc }: { doc: keyof typeof LEGAL }) {
  const d = LEGAL[doc];
  useEffect(() => { setMeta({ title: `${d.title} · Helm Terminals`, description: d.description, path: d.path }); }, [d]);
  return (
    <div className="pub">
      <a href="#pub-main" className="skip-link">Skip to content</a>
      <PublicHeader />
      <main id="pub-main" className="pub-wrap legal">
        <nav className="legal-nav" aria-label="Legal documents">
          {Object.entries(LEGAL).map(([k, v]) => <Link key={k} to={v.path} aria-current={k === doc ? "page" : undefined}>{v.title}</Link>)}
        </nav>
        <article>
          <h1>{d.title}</h1>
          <p className="muted small">Draft · last updated {LEGAL_UPDATED}</p>
          <Callout tone="warn">This is a draft prepared from how Helm works today. Items in brackets must be completed by the operator and the whole document reviewed by a qualified lawyer before it is relied on.</Callout>
          <p className="lead-sm">{d.intro}</p>
          <ol className="legal-toc" aria-label="Contents">{d.sections.map(([t], i) => <li key={t}><a href={`#s${i + 1}`}>{t}</a></li>)}</ol>
          {d.sections.map(([t, body], i) => (
            <section key={t} id={`s${i + 1}`} aria-labelledby={`h${i + 1}`}>
              <h2 id={`h${i + 1}`}>{i + 1}. {t}</h2>
              {body}
            </section>
          ))}
        </article>
      </main>
      <PublicFooter />
    </div>
  );
}
