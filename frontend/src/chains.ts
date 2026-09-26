/** Chain and launchpad metadata used across the UI. The API's /launchpads list is the source of truth for status. */

export type ChainId = "solana" | "base" | "bsc" | "robinhood" | "ethereum";

export interface Chain {
  id: ChainId;
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

export const chainOf = (id: string | null | undefined): Chain => CHAINS[(id as ChainId) ?? "solana"] ?? CHAINS.solana;
export const native = (network: string | null | undefined) => chainOf(network).native;

export function explorerUrl(network: string | null | undefined, type: "account" | "token" | "tx", value: string) {
  return chainOf(network).explorer[type] + value;
}

export const PAD_NAMES: Record<string, string> = {
  pumpfun: "Pump.fun", letsbonk: "LetsBonk", "meteora-dbc": "Meteora DBC", bags: "Bags", printr: "Printr",
  surge: "Surge", anoncoin: "Anoncoin", stonkfun: "Stonk.fun", clanker: "Clanker", bankr: "Bankr",
  "four-meme": "Four.meme", "flap-sh": "Flap.sh", "flap-sh-robinhood": "Flap.sh", pons: "Pons", "uniswap-v2": "Uniswap V2",
};
export const padName = (id: string) => PAD_NAMES[id] ?? id;

export interface Pad { id: string; network: string; status: string; quote_assets?: string[]; reason?: string }

/** Launchpads that need a token image to launch. */
export const NEEDS_IMAGE = new Set(["letsbonk", "bags"]);

/** Launchpads grouped by chain, in display order. */
export function padsByChain(pads: Pad[]) {
  return CHAIN_ORDER.map((c) => ({ chain: CHAINS[c], pads: pads.filter((p) => p.network === c) })).filter((g) => g.pads.length);
}

/** Wallet networks the signing service can create keys on today. */
export const MANAGED_NETWORKS: ChainId[] = ["solana"];
