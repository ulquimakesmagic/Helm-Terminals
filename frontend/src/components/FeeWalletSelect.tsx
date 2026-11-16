import { short } from '../api';
import { Field } from './ui';
import { useEffect } from 'react';

interface Wallet {id:string;network:string;kind:string;label:string;address:string}
export default function FeeWalletSelect({network,wallets,value,onChange}:{network:string;wallets:Wallet[];value:string;onChange:(value:string)=>void}) {
  const choices=wallets.filter(w=>w.network===network && w.kind==='managed');
  useEffect(()=>{if(value && !choices.some(w=>w.id===value))onChange('');},[network,value,wallets,onChange]);
  if(!['solana','base','ethereum','bsc','robinhood'].includes(network))return null;
  return <Field label="Pay network fees from" hint={network==='solana'?'The fee wallet pays SOL fees and any new recipient token-account rent.':'A gas allowance is transferred first. Its network fee is included in review; any unused allowance stays in the sending wallet.'}>
    <select aria-label="Pay network fees from" value={value} onChange={event=>onChange(event.target.value)}>
      <option value="">Auto · sender or another funded wallet</option>
      {choices.map(w=><option value={w.id} key={w.id}>{w.label || 'Wallet'} · {short(w.address)}</option>)}
    </select>
  </Field>;
}
