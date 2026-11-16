import { useEffect, useState } from 'react';
import { ChainMark, PadMark } from './marks';
import { Icon, Logo } from './ui';
import testToken from '../assets/test-launch-token.webp';

export default function TestLaunch({ compact = false }: { compact?: boolean }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true), [hovered, setHovered] = useState(false), [reduced, setReduced] = useState(false);
  useEffect(() => { const query=window.matchMedia('(prefers-reduced-motion: reduce)'); const sync=()=>setReduced(query.matches); sync(); query.addEventListener('change',sync); return ()=>query.removeEventListener('change',sync); },[]);
  useEffect(() => { if (!playing || hovered || reduced) return; const timer=window.setInterval(()=>{if(document.visibilityState==='visible')setStep(s=>(s+1)%3);},5000);return ()=>clearInterval(timer); },[playing,hovered,reduced]);
  const steps = ['Create', 'Launch', 'Manage'];
  return <div className={`test-launch ${compact ? 'test-launch-compact' : ''} ${playing && !hovered && !reduced ? 'motion-running' : 'motion-paused'}`} onMouseEnter={()=>setHovered(true)} onMouseLeave={()=>setHovered(false)} onFocusCapture={()=>setHovered(true)} onBlurCapture={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setHovered(false);}}>
    <div className="test-launch-bar"><span><i /><i /><i /></span><b>HELM / LAUNCHDESK</b>{!reduced&&<button type="button" className="demo-motion-control" aria-label={playing?'Pause demonstration motion':'Play demonstration motion'} onClick={()=>{setPlaying(!playing);if(!playing)setHovered(false);}}>{playing?'Pause':'Play'}</button>}<span className="badge">DEMO</span></div>
    <div className="demo-engine" aria-hidden="true"><div className="demo-helm-wheel"><Logo size={74}/><small>HELM</small></div><svg className="demo-signal" viewBox="0 0 180 30"><path d="M0 15H180"/><path className="demo-packet" d="M0 15H180"/></svg><div className="demo-token-object"><img src={testToken} width={160} height={160} alt=""/><span>$TEST</span></div><div className="demo-orbit-ring"/></div>
    <div className="test-token">
      <div><h2>Test Launch <span>$TEST</span></h2><p><ChainMark network="solana" size={16} />Solana<span>·</span><PadMark pad="pumpfun" size={16} />Pump.fun</p></div>
      <span className="test-token-status"><span className="dot" />Preview</span>
    </div>
    <div className="test-launch-tabs" role="group" aria-label="Explore the demonstration">
      {steps.map((s, i) => <button key={s} type="button" aria-pressed={step === i} className={step === i ? 'on' : ''} onClick={() => {setStep(i);setPlaying(false);}}><span>0{i + 1}</span>{s}<Icon name={i === 0 ? 'rocket' : i === 1 ? 'shield' : 'trend'} size={16} /></button>)}
    </div>
    <div className="test-launch-stage" key={step}>
      {step === 0 ? <><div className="demo-fields"><div><small>Token name</small><b>Test Launch</b></div><div><small>Ticker</small><b>$TEST</b></div></div><div className="demo-chain"><span className="muted small">Launch destination</span><b><ChainMark network="solana" size={20} />Solana<Icon name="arrowright" size={14} /><PadMark pad="pumpfun" size={20} />Pump.fun</b></div><div className="demo-check"><Icon name="check" size={15} />Your draft. Ready when you are.</div></>
        : step === 1 ? <><div className="demo-review"><Icon name="shield" size={34} /><h3>You have the final say.</h3><p>Review the wallet, amounts, network costs and transaction details before you confirm.</p></div><div className="demo-check"><Icon name="eye" size={15} />A preview comes before every signature.</div></>
        : <><div className="demo-chart-label"><b>$TEST workspace</b><span className="badge">Illustrative chart</span></div><svg className="demo-chart" viewBox="0 0 500 120" role="img" aria-label="Illustrative chart with no actual market data"><defs><linearGradient id="demo-area" x1="0" y1="0" x2="0" y2="1"><stop stopColor="currentColor" stopOpacity=".25"/><stop offset="1" stopColor="currentColor" stopOpacity="0"/></linearGradient></defs><path d="M0 100 L30 92 55 99 80 78 110 88 145 61 175 75 205 54 235 63 265 35 295 48 330 29 360 42 395 15 425 24 465 9 500 17 V120 H0Z" fill="url(#demo-area)"/><path d="M0 100 L30 92 55 99 80 78 110 88 145 61 175 75 205 54 235 63 265 35 295 48 330 29 360 42 395 15 425 24 465 9 500 17" fill="none" stroke="currentColor" strokeWidth="2"/></svg><div className="demo-check"><Icon name="wallet" size={15} />Wallets, trades and token operations. Together.</div></>}
    </div>
    <div className="test-launch-foot"><span>Interactive product preview</span><span>No real token or transaction<Icon name="lock" size={12} /></span></div>
  </div>;
}
