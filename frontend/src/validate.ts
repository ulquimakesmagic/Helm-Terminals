// Shared address shape checks. The backend re-validates everything it receives (checksums included);
// these give early, consistent feedback and keep the regexes — previously
// copied into several components — from drifting apart.

export const SOLANA_ADDR = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
export const EVM_ADDR = /^0x[0-9a-fA-F]{40}$/;

export const isSolanaAddr = (v: unknown): v is string => typeof v === "string" && SOLANA_ADDR.test(v);
export const isEvmAddr = (v: unknown): v is string => typeof v === "string" && EVM_ADDR.test(v);
// What most review UIs need: "does this look like an address on a Solana or EVM chain".
export const isAddress = (v: unknown): v is string => isSolanaAddr(v) || isEvmAddr(v);
