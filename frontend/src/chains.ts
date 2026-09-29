/** Chain and launchpad metadata used across the UI. The API's /launchpads list is the source of truth for status. */

export type ChainId = "solana" | "base" | "bsc" | "robinhood" | "ethereum";

export interface Chain {
  id: ChainId | ExtraChainId;
  name: string;
  short: string;
  native: string;
  color: string;
  explorer: { account: string; token: string; tx: string };
}

export const CHAINS: Record<ChainId, Chain> = {
  solana: {
    id: "solana", name: "Solana", short: "SOL", native: "SOL", color: "#14f195",
    explorer: { account: "https://solscan.io/account/", token: "https://solscan.io/token/", tx: "https://solscan.io/tx/" },
  },
  base: {
    id: "base", name: "Base", short: "BASE", native: "ETH", color: "#3b82f6",
    explorer: { account: "https://basescan.org/address/", token: "https://basescan.org/token/", tx: "https://basescan.org/tx/" },
  },
  bsc: {
    id: "bsc", name: "BNB Chain", short: "BNB", native: "BNB", color: "#f3ba2f",
    explorer: { account: "https://bscscan.com/address/", token: "https://bscscan.com/token/", tx: "https://bscscan.com/tx/" },
  },
  robinhood: {
    // Arbitrum Orbit L2, chain ID 4663, mainnet since July 2026. Gas is paid in ETH.
    id: "robinhood", name: "Robinhood Chain", short: "RH", native: "ETH", color: "#ccff00",
    explorer: {
      account: "https://robinhoodchain.blockscout.com/address/", token: "https://robinhoodchain.blockscout.com/token/",
      tx: "https://robinhoodchain.blockscout.com/tx/",
    },
  },
  ethereum: {
    id: "ethereum", name: "Ethereum", short: "ETH", native: "ETH", color: "#8b9cf4",
    explorer: { account: "https://etherscan.io/address/", token: "https://etherscan.io/token/", tx: "https://etherscan.io/tx/" },
  },
};

export const CHAIN_ORDER: ChainId[] = ["solana", "base", "bsc", "robinhood", "ethereum"];

/** TRON, NEAR and Arc: wallets, balances and transfers only, when the server enables them (GET /chains).
 * Explorer paths for NEAR (nearblocks) and Arc are the explorers' standard ones. */
export type ExtraChainId = "tron" | "near" | "arc" | "ton";
export const EXTRA_CHAINS: Record<ExtraChainId, Chain> = {
  tron: { id: "tron", name: "TRON", short: "TRX", native: "TRX", color: "#ff060a",
    explorer: { account: "https://tronscan.org/address/", token: "https://tronscan.org/token20/", tx: "https://tronscan.org/transaction/" } },
  near: { id: "near", name: "NEAR", short: "NEAR", native: "NEAR", color: "#00ec97",
    explorer: { account: "https://nearblocks.io/address/", token: "https://nearblocks.io/token/", tx: "https://nearblocks.io/txns/" } },
  arc: { id: "arc", name: "Arc", short: "ARC", native: "USDC", color: "#7b61ff",
    explorer: { account: "https://explorer.arc.io/address/", token: "https://explorer.arc.io/token/", tx: "https://explorer.arc.io/tx/" } },
  ton: { id: "ton", name: "TON", short: "TON", native: "TON", color: "#0098ea",
    explorer: { account: "https://tonviewer.com/", token: "https://tonviewer.com/", tx: "https://tonviewer.com/transaction/" } },
};
export const isExtraChain = (id: string | null | undefined): id is ExtraChainId => !!id && id in EXTRA_CHAINS;

export const chainOf = (id: string | null | undefined): Chain =>
  CHAINS[(id as ChainId) ?? "solana"] ?? EXTRA_CHAINS[id as ExtraChainId] ?? CHAINS.solana;
export const native = (network: string | null | undefined) => chainOf(network).native;

export function explorerUrl(network: string | null | undefined, type: "account" | "token" | "tx", value: string) {
  return chainOf(network).explorer[type] + value;
}

export const PAD_NAMES: Record<string, string> = {
  pumpfun: "Pump.fun", letsbonk: "LetsBonk", "meteora-dbc": "Meteora DBC", bags: "Bags", printr: "Printr",
  surge: "Surge", anoncoin: "Anoncoin", stonkfun: "Stonk.fun", clanker: "Clanker", bankr: "Bankr",
  "four-meme": "Four.meme", "flap-sh": "Flap.sh", "flap-sh-robinhood": "Flap.sh", "clanker-robinhood": "Clanker", pons: "Pons", "uniswap-v2": "Uniswap V2",
  sunpump: "SunPump", "meme-cooking": "Meme.Cooking", tolly: "Tolly", gaspump: "GasPump",
};
export const padName = (id: string) => PAD_NAMES[id] ?? id;

export interface Pad { id: string; network: string; status: string; quote_assets?: string[]; reason?: string }

/** Launchpads that need a token image to launch. */
export const NEEDS_IMAGE = new Set(["letsbonk", "bags", "flap-sh", "flap-sh-robinhood", "four-meme", "meteora-dbc", "meme-cooking", "tolly"]);

/** Launchpads grouped by chain, in display order. */
export function padsByChain(pads: Pad[]) {
  const order: string[] = [...CHAIN_ORDER, "tron", "near", "arc", "ton"];
  return order.map((c) => ({ chain: chainOf(c), pads: pads.filter((p) => p.network === c) })).filter((g) => g.pads.length);
}

/** Wallet networks the signing service can create keys on today. */
export const MANAGED_NETWORKS: ChainId[] = ["solana", "base", "bsc", "robinhood", "ethereum"];

/** The dollar stablecoin kept on each chain. Robinhood Chain has no native USDC; it uses Paxos' USDG. */
export const STABLE: Record<ChainId, string> = { solana: "USDC", base: "USDC", bsc: "USDC", robinhood: "USDG", ethereum: "USDC" };
export const stableOf = (id: string | null | undefined) => STABLE[chainOf(id).id as ChainId];
