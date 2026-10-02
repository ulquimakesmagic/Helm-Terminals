// Shared address shape checks. The backend re-validates everything it receives (checksums, NEAR
// account rules, TON forms); these give early, consistent feedback and keep the regexes — previously
// copied into several components — from drifting apart.

export const SOLANA_ADDR = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
export const EVM_ADDR = /^0x[0-9a-fA-F]{40}$/;
export const TRON_ADDR = /^T[1-9A-HJ-NP-Za-km-z]{33}$/;
export const NEAR_ACCOUNT = /^([a-z0-9._-]{2,64}|[0-9a-f]{64})$/;
export const TON_ADDR = /^([A-Za-z0-9_+/-]{48}|-?[01]:[0-9a-fA-F]{64})$/;

export const isSolanaAddr = (v: unknown): v is string => typeof v === "string" && SOLANA_ADDR.test(v);
export const isEvmAddr = (v: unknown): v is string => typeof v === "string" && EVM_ADDR.test(v);
// What most review UIs need: "does this look like an address on a Solana or EVM chain".
export const isAddress = (v: unknown): v is string => isSolanaAddr(v) || isEvmAddr(v);
