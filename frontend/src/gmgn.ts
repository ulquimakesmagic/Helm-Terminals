export type ChartInterval = "5" | "15" | "60" | "240" | "1D";
export const CHART_INTERVALS: [ChartInterval, string][] = [["5", "5m"], ["15", "15m"], ["60", "1H"], ["240", "4H"], ["1D", "1D"]];
const CHAINS: Record<string, string> = { solana: "sol", ethereum: "eth", bsc: "bsc" };

/** Only chains and parameters in GMGN's official embed guide. No pool or authentication parameters. */
export function gmgnChartUrl(network: string, token: string | null, interval: ChartInterval): string | null {
  const chain = Object.prototype.hasOwnProperty.call(CHAINS, network) ? CHAINS[network] : null;
  if (!chain || !token || !CHART_INTERVALS.some(([id]) => id === interval)) return null;
  if (!(network === "solana" ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/ : /^0x[0-9a-fA-F]{40}$/).test(token)) return null;
  return `https://www.gmgn.cc/kline/${chain}/${encodeURIComponent(token)}?${new URLSearchParams({ theme: "dark", interval })}`;
}
