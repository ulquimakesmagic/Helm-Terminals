import { useEffect, useState } from 'react';
import { api, errMsg, performanceMedia } from '../api';
import { chainOf } from '../chains';
import { useSession } from '../session';

export type PnlFacts = { title:string; symbolLine:string; value:string; tone:'up'|'down'|'neutral'; caption:string; foot?:string };
export type CardPreferences = { theme:'orbit'|'aurora'|'carbon'; show_name:boolean; show_amount:boolean; show_token:boolean; custom_background:boolean; position_x:number; position_y:number; zoom:number; overlay:number };
export type CardSettings = { preferences:CardPreferences; media:null|{mime:string;bytes:number;width:number;height:number;duration:number|null} };
export const DEFAULT_CARD:CardPreferences={theme:'orbit',show_name:true,show_amount:true,show_token:true,custom_background:false,position_x:50,position_y:50,zoom:1,overlay:.65};
type Media=HTMLImageElement|HTMLVideoElement;

function paint(g:CanvasRenderingContext2D,f:PnlFacts,p:CardPreferences,owner:string,media?:Media) {
  g.fillStyle='#060b10';g.fillRect(0,0,1200,630);
  if(media&&p.custom_background){
    const w=media instanceof HTMLVideoElement?media.videoWidth:media.naturalWidth,h=media instanceof HTMLVideoElement?media.videoHeight:media.naturalHeight;
    const scale=Math.max(1200/w,630/h)*p.zoom;g.drawImage(media,(1200-w*scale)*p.position_x/100,(630-h*scale)*p.position_y/100,w*scale,h*scale);
    g.fillStyle=`rgba(3,8,12,${p.overlay})`;g.fillRect(0,0,1200,630);
  }else{
    const glow=g.createRadialGradient(1020,70,0,1020,70,850);glow.addColorStop(0,p.theme==='aurora'?'#164653':p.theme==='carbon'?'#252f3a':'#104c3c');glow.addColorStop(1,'#060b10');g.fillStyle=glow;g.fillRect(0,0,1200,630);
    g.strokeStyle='rgba(140,220,200,.12)';g.lineWidth=1;
    if(p.theme==='carbon')for(let x=-630;x<1200;x+=36){g.beginPath();g.moveTo(x,0);g.lineTo(x+630,630);g.stroke();}
    else for(let r=150;r<900;r+=65){g.beginPath();g.ellipse(1100,80,r,r*.65,-.4,0,Math.PI*2);g.stroke();}
  }
  g.textBaseline='alphabetic';g.textAlign='left';g.fillStyle='#35d0a0';g.font="500 20px 'IBM Plex Mono',monospace";g.fillText('HELM / PERFORMANCE',64,72);
  g.fillStyle='#dce9e7';g.font="500 27px 'IBM Plex Sans',sans-serif";if(p.show_name)g.fillText(owner||'Helm trader',64,135,1050);
  if(p.show_token){g.fillStyle='#a7b5ba';g.font="400 24px 'IBM Plex Mono',monospace";g.fillText(`${f.title}  ${f.symbolLine}`,64,182,1050);}
  g.fillStyle=p.show_amount?(f.tone==='neutral'?'#a7b5ba':f.tone==='up'?'#55e3ac':'#ff8d94'):'#a7b5ba';g.font="500 100px 'IBM Plex Mono',monospace";g.fillText(p.show_amount?f.value:'Amount hidden',64,340,1060);
  g.fillStyle='#d1dddf';g.font="400 25px 'IBM Plex Sans',sans-serif";g.fillText(f.caption,64,406,1060);
  if(f.foot){g.fillStyle='#a7b5ba';g.font="400 21px 'IBM Plex Sans',sans-serif";g.fillText(f.foot,64,448,1060);}
  g.strokeStyle='rgba(167,181,186,.2)';g.beginPath();g.moveTo(64,508);g.lineTo(1136,508);g.stroke();g.fillStyle='#35d0a0';g.font="600 26px 'IBM Plex Sans',sans-serif";g.fillText('Helm Terminals',64,566);
  g.fillStyle='#a7b5ba';g.font="400 18px 'IBM Plex Mono',monospace";g.textAlign='right';g.fillText('YOUR OPERATION. YOUR RECORD.',1136,566);g.textAlign='left';
}
export function drawPnl(f:PnlFacts,p=DEFAULT_CARD,owner='Helm trader',media?:Media):string{
  const canvas=document.createElement('canvas');canvas.width=1200;canvas.height=630;paint(canvas.getContext('2d')!,f,p,owner,media);return canvas.toDataURL('image/png');
}
async function loadBackground(blob:Blob):Promise<Media>{
  const url=URL.createObjectURL(blob);
  try{
    if(blob.type.startsWith('video/')){
      const video=document.createElement('video');video.muted=true;video.playsInline=true;video.preload='auto';video.src=url;
      await new Promise<void>((resolve,reject)=>{video.onloadeddata=()=>resolve();video.onerror=()=>reject(new Error('Cannot decode this MP4. Try an H.264 MP4.'));video.load();});return video;
    }
    const image=new Image();image.src=url;await image.decode();return image;
  }catch(e){URL.revokeObjectURL(url);throw e;}
}
function release(media?:Media){if(!media)return;const url=media.src;if(media instanceof HTMLVideoElement){media.pause();media.removeAttribute('src');media.load();}URL.revokeObjectURL(url);}
async function resources(options?:CardPreferences,blob?:Blob|null,owner?:string){
  const settings=options?{preferences:options,media:null}:await api<CardSettings>('/profile/performance');
  const me=owner===undefined?await api('/auth/me'):null;
  const source=blob||(settings.preferences.custom_background&&settings.media?await performanceMedia():null);
  const media=source?await loadBackground(source):undefined;await document.fonts.ready;
  return {preferences:settings.preferences,media,owner:owner??me?.display_name??'Helm trader'};
}
export async function downloadPnl(f:PnlFacts,filename:string,options?:CardPreferences,blob?:Blob|null,owner?:string){
  const r=await resources(options,blob,owner);try{const a=document.createElement('a');a.download=filename;a.href=drawPnl(f,r.preferences,r.owner,r.media);a.click();}finally{release(r.media);}
}
export async function downloadPnlVideo(f:PnlFacts,p:CardPreferences,blob:Blob,owner:string){
  if(typeof MediaRecorder==='undefined')throw new Error('Video export is unavailable in this browser. Export a PNG instead.');
  const r=await resources(p,blob,owner),media=r.media;if(!(media instanceof HTMLVideoElement)){release(media);throw new Error('Upload an MP4 first');}
  const canvas=document.createElement('canvas');canvas.width=1200;canvas.height=630;const stream=canvas.captureStream(24),chunks:Blob[]=[];let frame=0,total=0,output:string|undefined;
  try{
    const type=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(t=>MediaRecorder.isTypeSupported(t));if(!type)throw new Error('WebM export is unavailable. Export a PNG instead.');
    const recorder=new MediaRecorder(stream,{mimeType:type,videoBitsPerSecond:2_000_000});
    const stopped=new Promise<void>((resolve,reject)=>{recorder.ondataavailable=e=>{total+=e.data.size;if(total<=20*1024*1024)chunks.push(e.data);else if(recorder.state==='recording')recorder.stop();};recorder.onstop=()=>resolve();recorder.onerror=()=>reject(new Error('Video export failed'));});
    const draw=()=>{paint(canvas.getContext('2d')!,f,p,owner,media);frame=requestAnimationFrame(draw);};draw();recorder.start(250);media.currentTime=0;
    const stop=()=>{if(recorder.state==='recording')recorder.stop();};const deadline=window.setTimeout(stop,31_000);media.onended=stop;
    try{await media.play();await stopped;}finally{clearTimeout(deadline);stop();}
    if(total>20*1024*1024)throw new Error('Export exceeded 20 MB. Try a shorter video.');
    output=URL.createObjectURL(new Blob(chunks,{type}));const a=document.createElement('a');a.href=output;a.download='helm-performance.webm';a.click();
  }finally{cancelAnimationFrame(frame);stream.getTracks().forEach(t=>t.stop());release(media);if(output)setTimeout(()=>URL.revokeObjectURL(output!),1000);}
}
export function PnlImage({facts,alt,preferences,media:blob,displayName}:{facts:PnlFacts;alt:string;preferences?:CardPreferences;media?:Blob|null;displayName?:string}){
  const {me}=useSession();const [src,setSrc]=useState<string|null>(null),[error,setError]=useState('');const key=JSON.stringify(facts),prefs=JSON.stringify(preferences),owner=displayName??me?.display_name??'Helm trader';
  useEffect(()=>{let active=true;resources(prefs?JSON.parse(prefs):undefined,blob,owner).then(r=>{try{if(active){setSrc(drawPnl(JSON.parse(key),r.preferences,r.owner,r.media));setError('');}}finally{release(r.media);}}).catch(e=>{if(active)setError(errMsg(e));});return()=>{active=false;};},[key,prefs,blob,owner]);
  if(error)return <p className="muted small" role="status">Card preview unavailable: {error}</p>;
  return src?<img src={src} alt={alt} style={{width:'100%',borderRadius:16,border:'1px solid var(--line)',display:'block'}}/>:<div className="skeleton" style={{aspectRatio:'1200/630'}} aria-label="Loading card preview"/>;
}
export function launchPnlFacts(l:{name:string;symbol:string;network:string},p:{net_sol_flow:number;confirmed_txs:number}):PnlFacts{
  const unit=chainOf(l.network).native;return {title:l.name,symbolLine:`$${l.symbol}`,value:`${p.net_sol_flow>=0?'+':''}${p.net_sol_flow.toFixed(3)} ${unit}`,tone:p.net_sol_flow>=0?'up':'down',caption:`Net ${unit} flow · ${p.confirmed_txs} confirmed transactions`,foot:'Net flow is not investment PnL. Unsold tokens are not valued. Fees included.'};
}
