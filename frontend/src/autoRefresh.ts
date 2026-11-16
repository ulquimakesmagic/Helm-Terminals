/** Visible-page refresh loop. A slow read never overlaps another scheduled read. */
export function startAutoRefresh(read: () => unknown | Promise<unknown>, interval: number, env = {
  visible: () => document.visibilityState === 'visible',
  every: (fn: () => void, ms: number) => window.setInterval(fn, ms),
  cancel: (id: number) => window.clearInterval(id),
  listen: (fn: () => void) => {
    document.addEventListener('visibilitychange', fn);
    window.addEventListener('focus', fn);
    window.addEventListener('online', fn);
    return () => { document.removeEventListener('visibilitychange', fn); window.removeEventListener('focus', fn); window.removeEventListener('online', fn); };
  },
}) {
  let active = true, busy = false;
  const refresh = async () => {
    if (!active || busy || !env.visible()) return;
    busy = true;
    try { await read(); } catch { /* The owning view reports failed reads; the next tick retries. */ }
    finally { busy = false; }
  };
  const timer = env.every(() => { void refresh(); }, interval);
  const unlisten = env.listen(() => { void refresh(); });
  return () => { active = false; env.cancel(timer); unlisten(); };
}
