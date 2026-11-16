import { useEffect, useRef } from 'react';
import { startAutoRefresh } from '../autoRefresh';

export function useAutoRefresh(read: () => unknown | Promise<unknown>, interval = 30_000) {
  const latest = useRef(read);
  latest.current = read;
  useEffect(() => startAutoRefresh(() => latest.current(), interval), [interval]);
}
