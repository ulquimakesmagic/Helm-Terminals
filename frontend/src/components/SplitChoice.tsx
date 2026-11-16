export type SplitMode='total'|'percent'|'custom'|'weighted'|'random';
export default function SplitChoice({value,onChange}:{value:SplitMode;onChange:(value:SplitMode)=>void}){
  return <div className="split-choices" role="group" aria-label="Split mode">{([
    ['total','Even · total'],['percent','Even · % balance'],['custom','Custom amounts'],['weighted','Variable weights'],['random','Random'],
  ] as [SplitMode,string][]).map(([id,label])=><button key={id} type="button" aria-pressed={value===id} className={value===id?'on':''} onClick={()=>onChange(id)}>{label}</button>)}</div>;
}
