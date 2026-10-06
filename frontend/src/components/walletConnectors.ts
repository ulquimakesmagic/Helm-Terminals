/*
 * The user's own wallet on Arc. Helm never sees a key: it asks the wallet to sign a Helm challenge (to prove
 * ownership) or a transfer the user already reviewed in Helm. Connecting authorises nothing by itself.
 *
 * Arc: any EIP-1193 browser wallet (window.ethereum): eth_requestAccounts, chain check / switch, personal_sign,
 *      eth_sendTransaction.
 */

export class WalletError extends Error {
  declined: boolean;
  /** true when nothing can have been broadcast (declined, or a check before the wallet prompt). false means the
   * wallet may have sent it, so the transfer is left for a look. */
  nothingSent: boolean;
  constructor(message: string, declined = false, nothingSent = true) { super(message); this.declined = declined; this.nothingSent = declined || nothingSent; }
}

export interface NetInfo { id: string; name: string; family: string; chain_id?: number | null }

const w = window as any;
const declinedCodes = new Set([4001, "ACTION_REJECTED"]);
const wrap = (e: any, mayHaveSent = false): WalletError => {
  const msg = String(e?.message ?? e ?? "The wallet returned an error");
  const declined = declinedCodes.has(e?.code) || /reject|denied|cancel|declin/i.test(msg);
  return new WalletError(declined ? "You declined in your wallet. Nothing was signed." : msg, declined, !mayHaveSent);
};

const ARC_PARAMS: Record<number, any> = {
  5042: { chainName: "Arc", rpcUrls: ["https://rpc.mainnet.arc.io"], blockExplorerUrls: ["https://explorer.arc.io"] },
};

async function evmChain(): Promise<number> {
  return parseInt(await w.ethereum.request({ method: "eth_chainId" }), 16);
}

async function evmConnect(net: NetInfo): Promise<string> {
  if (!w.ethereum?.request) throw new WalletError("No browser EVM wallet found. Install one that supports custom networks (for example MetaMask or Rabby), then try again.");
  let accounts: string[];
  try { accounts = await w.ethereum.request({ method: "eth_requestAccounts" }); } catch (e) { throw wrap(e); }
  if (!accounts?.length) throw new WalletError("The wallet shared no account.");
  const want = net.chain_id!;
  if ((await evmChain()) !== want) {
    const hex = "0x" + want.toString(16);
    try {
      await w.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
    } catch (e: any) {
      if (e?.code !== 4902) throw wrap(e);
      try {
        await w.ethereum.request({ method: "wallet_addEthereumChain", params: [{ chainId: hex, ...ARC_PARAMS[want],
          nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 } }] });
      } catch (e2) { throw wrap(e2); }
    }
    if ((await evmChain()) !== want) throw new WalletError(`The wallet is still on another chain; switch it to ${net.name} (chain ID ${want}).`);
  }
  return accounts[0];
}

const hexOf = (s: string) => "0x" + Array.from(new TextEncoder().encode(s)).map((b) => b.toString(16).padStart(2, "0")).join("");

function evmOnly(net: NetInfo) {
  if (net.family !== "evm") throw new WalletError(`${net.name} wallets can't be linked in Helm.`);
}

/** Ask the wallet which account it will use (and make sure it's on the right network). */
export async function connectWallet(net: NetInfo, _userId: string): Promise<string> {
  evmOnly(net);
  return evmConnect(net);
}

/** Sign Helm's ownership challenge. Returns what /wallet-proof/verify needs. */
export async function signChallenge(net: NetInfo, _userId: string, address: string, ch: { message: string; nonce: string; recipient: string }) {
  evmOnly(net);
  try {
    const signature = await w.ethereum.request({ method: "personal_sign", params: [hexOf(ch.message), address] });
    return { signature, chain_id: await evmChain() };
  } catch (e) { throw wrap(e); }
}

/** Have the wallet approve and send a transfer Helm built; it returns the transaction hash. */
export async function signTransfer(net: NetInfo, _userId: string, from: string, request: any): Promise<{ tx_hash?: string }> {
  evmOnly(net);
  await evmConnect(net);
  const accounts: string[] = await w.ethereum.request({ method: "eth_accounts" });
  if (!accounts.map((a) => a.toLowerCase()).includes(from.toLowerCase())) throw new WalletError(`Switch the wallet to ${from} first.`);
  try {
    const tx_hash = await w.ethereum.request({ method: "eth_sendTransaction",
      params: [{ from, to: request.to, data: request.data, value: request.value, chainId: "0x" + Number(request.chain_id ?? net.chain_id).toString(16) }] });
    if (typeof tx_hash !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(tx_hash)) throw new WalletError("The wallet didn't return the transaction; check its history before sending again.", false, false);
    return { tx_hash };
  } catch (e) { throw e instanceof WalletError ? e : wrap(e, true); }
}

/** Nothing to forget in the browser: EVM wallets are disconnected in the wallet itself. */
export async function forgetWallet(_net: NetInfo, _userId: string) {
  return undefined;
}

export const WALLET_APP: Record<string, string> = { evm: "a browser EVM wallet" };

/** Link the user's own wallet: connect, sign Helm's one-time challenge, and have Helm verify it. Links, never spends. */
export async function linkOwnWallet(net: NetInfo, userId: string): Promise<void> {
  const { api } = await import("../api");
  const address = await connectWallet(net, userId);
  const ch = await api<any>(`/chains/${net.id}/wallet-proof/challenge`, { body: { address, domain: window.location.host } });
  const sig: any = await signChallenge(net, userId, ch.address, ch);
  await api(`/chains/${net.id}/wallet-proof/verify`, { body: { address: ch.address, domain: window.location.host,
    nonce: ch.nonce, signature: sig.signature, chain_id: sig.chain_id ?? null } });
}
