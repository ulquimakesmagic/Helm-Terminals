import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errMsg, fmt, usd } from '../api';
import { useSession } from '../session';
import { ProfileAvatar } from '../components/profile';
import ReferralCard from '../components/Referrals';
import { downloadPnl, PnlFacts, PnlImage } from '../components/pnl';
import { Callout, Card, Field, Icon, Loading, Modal, Spinner, StatusBadge, useToast } from '../components/ui';

export default function Profile(){
  const { refresh }=useSession();const toast=useToast();
  const [d,setD]=useState<any>(null),[error,setError]=useState(''),[editing,setEditing]=useState(false),[name,setName]=useState(''),[handle,setHandle]=useState(''),[busy,setBusy]=useState(false);
  const load=()=>api('/profile').then(r=>{setD(r);setName(r.profile.display_name);setHandle(r.profile.handle||'');setError('');}).catch(e=>setError(errMsg(e)));
  useEffect(()=>{load();},[]);
  const save=async()=>{setBusy(true);try{await api('/profile',{method:'PATCH',body:{display_name:name,handle,is_public:false}});await load();await refresh();setEditing(false);toast('good','Account identity saved');}catch(e){setError(errMsg(e));}finally{setBusy(false);}};
  if(!d)return error?<Callout tone="err">{error}</Callout>:<Loading/>;
  const p=d.profile,s=d.stats,flow=s.net_flow?.usd;
  const facts:PnlFacts={title:'Account performance',symbolLine:'MEASURED NET FLOW',value:flow==null?'No recorded flow':`${flow>=0?'+':'−'}${usd(Math.abs(flow))}`,tone:flow==null?'neutral':flow>=0?'up':'down',caption:s.net_flow?.priced?'Recorded native-coin flows at current prices':'Partial valuation — unpriced or unrecorded flows excluded',foot:'Net flow is not investment PnL. Unsold tokens are not valued.'};
  return <>{error&&<Callout tone="err">{error}</Callout>}<header className="private-profile-hero"><ProfileAvatar name={p.display_name} hue={p.avatar_hue} size={48}/><div><span className="eyebrow">YOUR PRIVATE ACCOUNT</span><h1>{p.display_name}</h1><p className="muted">{p.handle?`@${p.handle} · `:''}Your identity on exported performance cards. Your account is visible only to you.</p></div><button className="btn secondary" onClick={()=>setEditing(true)}><Icon name="user" size={16}/>Edit identity</button></header>
    <div className="metrics" aria-label="Account summary">{[['Live launches',s.launches.live],['Confirmed trades',s.trades.count],['Managed wallets',s.wallets.managed],['Recorded fees',`${fmt(s.fees_paid_sol,6)} SOL`]].map(([label,value])=><div className="metric" key={label}><span className="k">{label}</span><strong className="num">{value}</strong>{label==='Recorded fees'&&<small className="muted">Solana fee ledger only</small>}</div>)}</div>
    <div className="grid g-2" style={{alignItems:'start'}}><Card className="profile-performance" title="Your performance record" icon="trend" sub="Measured data, with its scope kept visible"><div className="stack"><PnlImage facts={facts} alt="Your private account performance card"/><div className="row"><button className="btn secondary" onClick={()=>downloadPnl(facts,'helm-performance.png').catch(e=>toast('bad',errMsg(e)))}><Icon name="download" size={15}/>Export card</button><Link className="btn ghost" to="/settings?section=performance">Customize appearance</Link></div><p className="muted small">This card measures net flows. Realized and unrealized investment PnL require complete cost-basis and position data and are not calculated here.</p></div></Card>
    <Card title="Recent launches" icon="rocket" sub="Your latest work across chains"><div className="stack">{d.recent_launches.length?d.recent_launches.map((l:any)=><Link className="row between" key={l.id} to={`/launches/${l.id}`} style={{paddingBlock:12,borderBottom:'1px solid var(--line)'}}><span><b>{l.name}</b><span className="muted small"> · ${l.symbol}</span></span><StatusBadge status={l.status}/></Link>):<p className="muted">Your launches will appear here.</p>}<Link className="btn secondary" to="/launches">Open your launches<Icon name="arrowright" size={15}/></Link></div></Card></div>
    <ReferralCard data={d.referral} onChange={load}/>
    {editing&&<Modal title="Your account identity" sub="Used inside Helm and on cards you choose to export. No public profile is created." onClose={()=>setEditing(false)} lock={busy} foot={<><button className="btn secondary" disabled={busy} onClick={()=>setEditing(false)}>Cancel</button><button className="btn" disabled={busy||!name.trim()} onClick={save}>{busy&&<Spinner/>}Save identity</button></>}><div className="stack"><Field label="Display name" hint="This name appears on performance cards."><input maxLength={40} value={name} onChange={e=>setName(e.target.value)}/></Field><Field label="Username" hint="Optional: 3–20 letters, numbers or underscores."><input maxLength={20} value={handle} onChange={e=>setHandle(e.target.value)}/></Field>{error&&<Callout tone="err">{error}</Callout>}</div></Modal>}
  </>;
}
