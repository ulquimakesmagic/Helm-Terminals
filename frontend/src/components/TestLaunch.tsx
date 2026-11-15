import { useState } from 'react';
import { ChainMark, PadMark } from './marks';
import { Icon } from './ui';

export default function TestLaunch({ compact = false }: { compact?: boolean }) {
  const [step, setStep] = useState(0);
  const steps = ['Create', 'Launch', 'Manage'];
  return <div className={`test-launch ${compact ? 'test-launch-compact' : ''}`}>
    <div className="test-launch-bar"><span><i /><i /><i /></span><b>HELM / LAUNCHDESK</b><span className="badge">DEMONSTRATION</span></div>
    <div className="test-token">
      <div className="test-token-art" aria-hidden="true"><div className="test-orbit" /><div className="test-orbit second" /><span>T</span></div>
      <div><span className="eyebrow">YOUR NEXT IDEA</span><h2>Test Launch <span>$TEST</span></h2><p><ChainMark network="solana" size={16} />Solana<span>·</span><PadMark pad="pumpfun" size={16} />Pump.fun</p></div>
      <span className="test-token-status"><span className="dot" />Preview</span>
    </div>
    <div className="test-launch-tabs" role="group" aria-label="Explore the demonstration">
      {steps.map((s, i) => <button key={s} type="button" aria-pressed={step === i} className={step === i ? 'on' : ''} onClick={() => setStep(i)}><span>0{i + 1}</span>{s}<Icon name={i === 0 ? 'rocket' : i === 1 ? 'shield' : 'trend'} size={16} /></button>)}
    </div>
    <div className="test-launch-stage" key={step}>
      {step === 0 ? <><div className="demo-fields"><div><small>Token name</small><b>Test Launch</b></div><div><small>Ticker</small><b>$TEST</b></div></div><div className="demo-chain"><span className="muted small">Launch destination</span><b><ChainMark network="solana" size={20} />Solana<Icon name="arrowright" size={14} /><PadMark pad="pumpfun" size={20} />Pump.fun</b></div><div className="demo-check"><Icon name="check" size={15} />Your draft. Ready when you are.</div></>
        : step === 1 ? <><div className="demo-review"><Icon name="shield" size={34} /><h3>You have the final say.</h3><p>Review the wallet, amounts, network costs and transaction details before you confirm.</p></div><div className="demo-check"><Icon name="eye" size={15} />A preview comes before every signature.</div></>
        : <><div className="demo-chart-label"><b>$TEST workspace</b><span className="badge">Illustrative chart</span></div><svg className="demo-chart" viewBox="0 0 500 120" role="img" aria-label="Illustrative chart with no actual market data"><defs><linearGradient id="demo-area" x1="0" y1="0" x2="0" y2="1"><stop stopColor="currentColor" stopOpacity=".25"/><stop offset="1" stopColor="currentColor" stopOpacity="0"/></linearGradient></defs><path d="M0 100 L30 92 55 99 80 78 110 88 145 61 175 75 205 54 235 63 265 35 295 48 330 29 360 42 395 15 425 24 465 9 500 17 V120 H0Z" fill="url(#demo-area)"/><path d="M0 100 L30 92 55 99 80 78 110 88 145 61 175 75 205 54 235 63 265 35 295 48 330 29 360 42 395 15 425 24 465 9 500 17" fill="none" stroke="currentColor" strokeWidth="2"/></svg><div className="demo-check"><Icon name="wallet" size={15} />Wallets, trades and token operations. Together.</div></>}
    </div>
    <div className="test-launch-foot"><span>Interactive product preview</span><span>No real token or transaction<Icon name="lock" size={12} /></span></div>
  </div>;
}
