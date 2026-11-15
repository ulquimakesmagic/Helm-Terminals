import { useEffect, useId, useRef, useState } from 'react';
import { chainOf } from '../chains';
import { ChainMark } from './marks';
import { Icon } from './ui';

export default function ChainSelect({ value, onChange, networks, counts, label = 'Filter by chain' }: {
  value: string; onChange: (id: string) => void; networks: string[]; counts?: Record<string, number>; label?: string;
}) {
  const [open, setOpen] = useState(false), [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null), search = useRef<HTMLInputElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const outside = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  const options = ['all', ...networks].filter((n, i, a) => a.indexOf(n) === i && (n === 'all' ? 'all chains' : chainOf(n).name).toLowerCase().includes(query.toLowerCase()));
  return <div className="chain-select" ref={ref} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
    <button ref={trigger} type="button" className="btn secondary chain-select-trigger" aria-label={label} aria-expanded={open} aria-controls={id}
      onClick={() => { setOpen(!open); setQuery(''); }}>
      {value === 'all' ? <Icon name="globe" size={18} /> : <ChainMark network={value} size={20} />}
      <span>{value === 'all' ? 'All chains' : chainOf(value).name}</span><Icon name="chevron" size={13} />
    </button>
    {open && <div className="chain-select-menu" id={id} role="group" aria-label={label}>
      <input ref={search} value={query} onChange={e => setQuery(e.target.value)} placeholder="Find a chain…" aria-label="Search chains" />
      <div className="chain-select-options">{options.map(n => <button key={n} type="button" aria-pressed={value === n} onClick={() => { onChange(n); setOpen(false); trigger.current?.focus(); }}>
        {n === 'all' ? <Icon name="globe" size={20} /> : <ChainMark network={n} size={20} />}<span>{n === 'all' ? 'All chains' : chainOf(n).name}</span>
        {counts?.[n] !== undefined && <small>{counts[n]}</small>}{n === value && <Icon name="check" size={14} />}
      </button>)}{!options.length && <p className="muted small">No chains match.</p>}</div>
    </div>}
  </div>;
}
