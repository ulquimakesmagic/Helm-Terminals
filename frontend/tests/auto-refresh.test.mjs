import assert from 'node:assert/strict';
import test from 'node:test';
import {startAutoRefresh} from '../src/autoRefresh.ts';

function environment(){
 let tick, resume, visible=true, cancelled=false, removed=false;
 return {env:{visible:()=>visible,every:(fn,ms)=>{assert.equal(ms,30000);tick=fn;return 7;},cancel:id=>{assert.equal(id,7);cancelled=true;},listen:fn=>{resume=fn;return()=>{removed=true;};}},
  tick:()=>tick(),resume:()=>resume(),hide:()=>{visible=false;},show:()=>{visible=true;},cleaned:()=>cancelled&&removed};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));
test('refreshes on timer and return, skips hidden tabs, and disposes listeners',async()=>{
 const e=environment();let reads=0;const stop=startAutoRefresh(()=>{reads++;},30000,e.env);
 e.tick();await settled();assert.equal(reads,1);e.hide();e.tick();e.resume();await settled();assert.equal(reads,1);
 e.show();e.resume();await settled();assert.equal(reads,2);stop();e.tick();await settled();assert.equal(reads,2);assert.equal(e.cleaned(),true);
});
test('slow reads never overlap and failed reads retry on the next tick',async()=>{
 const e=environment();let reads=0,finish;const stop=startAutoRefresh(()=>{reads++;return new Promise((resolve,reject)=>{finish=reads===1?()=>reject(Error('offline')):resolve;});},30000,e.env);
 e.tick();e.tick();e.resume();assert.equal(reads,1);finish();await settled();e.tick();assert.equal(reads,2);finish();await settled();stop();
});
