import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errMsg, performanceMedia } from '../api';
import { useSession } from '../session';
import { Callout, Card, Field, Icon, Loading, Spinner, useToast } from './ui';
import { CardPreferences, CardSettings, DEFAULT_CARD, downloadPnl, downloadPnlVideo, PnlFacts, PnlImage } from './pnl';

const preview:PnlFacts={title:'Test Launch',symbolLine:'$TEST · DEMONSTRATION',value:'+0.250 SOL',tone:'up',caption:'Illustrative net flow — not your actual performance',foot:'Net flow is not investment PnL. Unsold tokens are not valued.'};
export default function PerformanceSettings(){
  const {me}=useSession(),toast=useToast();const [p,setP]=useState<CardPreferences>(DEFAULT_CARD),[info,setInfo]=useState<CardSettings['media']>(null),[media,setMedia]=useState<Blob|null>(null),[ready,setReady]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState('');
  useEffect(()=>{let active=true;api<CardSettings>('/profile/performance').then(async r=>{const blob=r.media?await performanceMedia():null;if(active){setP(r.preferences);setInfo(r.media);setMedia(blob);setReady(true);}}).catch(e=>{if(active)setError(errMsg(e));});return()=>{active=false;};},[]);
  const change=<K extends keyof CardPreferences>(key:K,value:CardPreferences[K])=>{setP(old=>({...old,[key]:value}));setSaved('');};
  const save=async()=>{setBusy(true);setError('');try{const r=await api<CardSettings>('/profile/performance',{method:'PUT',body:p});setP(r.preferences);setSaved('Preferences saved. These apply to your performance cards.');}catch(e){setError(errMsg(e));}finally{setBusy(false);}};
  const upload=async(file?:File)=>{
    if(!file)return;if(file.size>10*1024*1024){setError('Backgrounds must be at most 10 MB');return;}
    setBusy(true);setError('');try{const r=await performanceMedia(file);setP(r.preferences);setInfo(r.media);setMedia(await performanceMedia());setSaved('Background saved privately to your account.');}catch(e){setError(errMsg(e));}finally{setBusy(false);}
  };
  const remove=async()=>{setBusy(true);try{const r=await performanceMedia(undefined,true);setP(r.preferences);setInfo(null);setMedia(null);setSaved('Custom background removed.');}catch(e){setError(errMsg(e));}finally{setBusy(false);}};
  const exportCard=async(video=false)=>{setBusy(true);try{if(video&&media)await downloadPnlVideo(preview,p,media,me?.display_name||'Helm trader');else await downloadPnl(preview,'helm-card-preview.png',p,media,me?.display_name||'Helm trader');toast('good','Card exported');}catch(e){setError(errMsg(e));}finally{setBusy(false);}};
  return <><div className="page-head"><div><span className="eyebrow">MAKE YOUR RECORD YOUR OWN</span><h1>Performance cards</h1><p className="muted">Choose a visual identity for the records you export. Your profile stays private.</p></div></div>
    {error&&<Callout tone="err">{error}</Callout>}{!ready&&!error?<Loading/>:<div className="grid g-2" style={{alignItems:'start'}}>
      <Card title="Your card style" icon="layers"><div className="stack">
        <div className="performance-themes" role="group" aria-label="Card theme">{(['orbit','aurora','carbon'] as const).map(theme=><button type="button" key={theme} className={`theme-${theme}`} aria-pressed={p.theme===theme} onClick={()=>change('theme',theme)}><b>{theme[0].toUpperCase()+theme.slice(1)}</b><div className="small">Helm / Performance</div></button>)}</div>
        <Field label="Custom background" hint="PNG/JPEG up to 5 MB, or MP4 up to 10 MB and 30 seconds. H.264 MP4 works in most browsers."><input type="file" accept="image/png,image/jpeg,video/mp4" disabled={busy} onChange={e=>{void upload(e.target.files?.[0]);e.target.value='';}}/></Field>
        {info&&<><p className="muted small">{info.width} × {info.height} · {(info.bytes/1024/1024).toFixed(2)} MB{info.duration?` · ${info.duration}s`:''}</p>
          <label className="row"><input type="checkbox" checked={p.custom_background} onChange={e=>change('custom_background',e.target.checked)}/>Use my uploaded background</label>
          <button type="button" className="btn secondary" onClick={remove} disabled={busy}><Icon name="trash" size={14}/>Remove background</button></>}
        {info&&p.custom_background&&<div className="stack">{([['position_x','Horizontal position',0,100,1],['position_y','Vertical position',0,100,1],['zoom','Zoom',1,2.5,.05],['overlay','Dark overlay',.5,.95,.05]] as const).map(([key,label,min,max,step])=><Field key={key} label={label}><input type="range" min={min} max={max} step={step} value={p[key]} onChange={e=>change(key,+e.target.value)}/></Field>)}</div>}
        <fieldset className="stack" style={{border:0,padding:0}}><legend className="small text-2">Included in exports</legend>{([['show_name','Display name'],['show_token','Token details'],['show_amount','Financial amount']] as const).map(([key,label])=><label className="row" key={key}><input type="checkbox" checked={p[key]} onChange={e=>change(key,e.target.checked)}/>{label}</label>)}</fieldset>
        <p className="muted small">Emails and wallet addresses are never included. Measurement notes stay visible. Change your display name in <Link to="/profile">Profile</Link>.</p>
        <button className="btn" disabled={busy||!ready} onClick={save}>{busy?<Spinner/>:<Icon name="check" size={15}/>}Save preferences</button>{saved&&<p className="good small" role="status">{saved}</p>}
      </div></Card>
      <Card title="Export preview" icon="eye" sub="Demonstration figures — your actual cards use recorded data"><div className="stack"><PnlImage facts={preview} alt="Demonstration performance card" preferences={p} media={media} displayName={me?.display_name||'Helm trader'}/><div className="row"><button className="btn secondary" disabled={busy} onClick={()=>exportCard()}><Icon name="download" size={15}/>Export PNG</button>{info?.mime==='video/mp4'&&p.custom_background&&<button className="btn secondary" disabled={busy} onClick={()=>exportCard(true)}>Export video</button>}</div><p className="muted small">Video backgrounds use a static frame in the preview. Video exports are muted WebM files where your browser supports recording; PNG is always available.</p></div></Card>
    </div>}</>;
}
