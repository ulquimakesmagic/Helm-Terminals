/*
 * The user's own wallets on TRON, Arc and NEAR. Helm never sees a key: it asks the wallet to sign a Helm challenge
 * (to prove ownership) or a transfer the user already reviewed in Helm. Connecting authorises nothing by itself.
 *
 * TRON: TronLink (window.tronLink / tronWeb): tron_requestAccounts, signMessageV2, trx.sign.
 * Arc: any EIP-1193 browser wallet (window.ethereum): eth_requestAccounts, chain check / switch, personal_sign,
 *      eth_sendTransaction.
 * NEAR: NEAR Connect (@hot-labs/near-connect): signMessage (NEP-413) and signAndSendTransaction. Its session is
 *      stored under keys that carry the Helm user id, so another Helm account on this browser doesn't inherit it.
 */

export class WalletError extends Error {
  declined: boolean;
  /** true when nothing can have been broadcast (declined, a check before the wallet prompt, or a TRON wallet, which
   * only signs: Helm broadcasts). false means the wallet may have sent it, so the transfer is left for a look. */
  nothingSent: boolean;
  constructor(message: string, declined = false, nothingSent = true) { super(message); this.declined = declined; this.nothingSent = declined || nothingSent; }
}

export interface NetInfo { id: string; name: string; family: string; testnet: boolean; chain_id?: number | null }

const w = window as any;
const declinedCodes = new Set([4001, "ACTION_REJECTED"]);
const wrap = (e: any, mayHaveSent = false): WalletError => {
  const msg = String(e?.message ?? e ?? "The wallet returned an error");
  const declined = declinedCodes.has(e?.code) || /reject|denied|cancel|declin/i.test(msg);
  return new WalletError(declined ? "You declined in your wallet. Nothing was signed." : msg, declined, !mayHaveSent);
};

// --- TRON (TronLink) -----------------------------------------------------------------------------------------

function tronWeb(): any {
  return w.tronLink?.tronWeb || w.tronWeb;
}

function tronNetworkOf(tw: any): "tron" | "tron-nile" | "other" {
  const host = String(tw?.fullNode?.host ?? "");
  if (/nile/i.test(host)) return "tron-nile";
  if (/shasta/i.test(host)) return "other";
  return /trongrid\.io|tron/i.test(host) ? "tron" : "other";
}

async function tronConnect(net: NetInfo): Promise<string> {
  if (!w.tronLink && !w.tronWeb) throw new WalletError("TronLink isn't installed in this browser. Install it from tronlink.org, then try again.");
  try {
    if (w.tronLink?.request) {
      const r = await w.tronLink.request({ method: "tron_requestAccounts" });
      if (r && r.code && r.code !== 200) throw new WalletError(r.code === 4001 ? "You declined in TronLink." : r.message || "TronLink refused", r.code === 4001);
    }
  } catch (e) { throw e instanceof WalletError ? e : wrap(e); }
  const tw = tronWeb();
  const address = tw?.defaultAddress?.base58;
  if (!address) throw new WalletError("Unlock TronLink and choose an account, then try again.");
  const on = tronNetworkOf(tw);
  if (on !== net.id) throw new WalletError(`TronLink is on ${on === "tron-nile" ? "the Nile testnet" : on === "tron" ? "TRON mainnet" : "another network"}; switch it to ${net.name} and try again.`);
  return address;
}

// --- Arc (EIP-1193) ------------------------------------------------------------------------------------------

const ARC_PARAMS: Record<number, any> = {
  5042: { chainName: "Arc", rpcUrls: ["https://rpc.mainnet.arc.io"], blockExplorerUrls: ["https://explorer.arc.io"] },
  5042002: { chainName: "Arc Testnet", rpcUrls: ["https://rpc.testnet.arc.io"], blockExplorerUrls: ["https://explorer.testnet.arc.io"] },
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

// --- NEAR (NEAR Connect) -------------------------------------------------------------------------------------

let nearConn: { key: string; c: any } | null = null;

async function near(net: NetInfo, userId: string) {
  const key = `${userId}:${net.testnet ? "testnet" : "mainnet"}`;
  if (nearConn?.key === key) return nearConn.c;
  if (nearConn && !nearConn.key.startsWith(`${userId}:`)) { window.location.reload(); throw new WalletError("switching accounts"); }
  const { NearConnector } = await import("@hot-labs/near-connect");
  const pre = `helm-near:${userId}:`;
  const storage = {
    get: async (k: string) => { try { return localStorage.getItem(pre + k); } catch { return null; } },
    set: async (k: string, v: string) => { try { localStorage.setItem(pre + k, v); } catch { /* private mode */ } },
    remove: async (k: string) => { try { localStorage.removeItem(pre + k); } catch { /* private mode */ } },
  };
  const c = new NearConnector({ network: net.testnet ? "testnet" : "mainnet", storage, features: { signMessage: true, signAndSendTransaction: true } } as any);
  nearConn = { key, c };
  return c;
}

async function nearAccount(net: NetInfo, userId: string): Promise<{ wallet: any; accountId: string }> {
  const c = await near(net, userId);
  try {
    let got = await c.getConnectedWallet().catch(() => null);
    if (!got) { await c.connect(); got = await c.getConnectedWallet(); }
    const accountId = got?.accounts?.[0]?.accountId;
    if (!accountId) throw new WalletError("The NEAR wallet shared no account.");
    return { wallet: got.wallet, accountId };
  } catch (e) { throw e instanceof WalletError ? e : wrap(e); }
}

// --- one interface for the settings page and the send dialog ---------------------------------------------------

/** Ask the wallet which account it will use (and make sure it's on the right network). */
export async function connectWallet(net: NetInfo, userId: string): Promise<string> {
  if (net.family === "tron") return tronConnect(net);
  if (net.family === "evm") return evmConnect(net);
  if (net.family === "near") return (await nearAccount(net, userId)).accountId;
  throw new WalletError(`${net.name} wallets connect through TON Connect`);
}

/** Sign Helm's ownership challenge. Returns what /wallet-proof/verify needs. */
export async function signChallenge(net: NetInfo, userId: string, address: string, ch: { message: string; nonce: string; recipient: string }) {
  if (net.family === "tron") {
    try { return { signature: await tronWeb().trx.signMessageV2(ch.message) }; } catch (e) { throw wrap(e); }
  }
  if (net.family === "evm") {
    try {
      const signature = await w.ethereum.request({ method: "personal_sign", params: [hexOf(ch.message), address] });
      return { signature, chain_id: await evmChain() };
    } catch (e) { throw wrap(e); }
  }
  const { wallet } = await nearAccount(net, userId);
  const nonce = new Uint8Array(ch.nonce.match(/../g)!.map((h) => parseInt(h, 16)));
  try {
    const s = await wallet.signMessage({ message: ch.message, recipient: ch.recipient, nonce, signerId: address });
    if (s?.accountId && s.accountId !== address) throw new WalletError(`The wallet signed as ${s.accountId}, not ${address}. Switch accounts in the wallet and try again.`);
    return { signature: s.signature, public_key: s.publicKey, signed_as: s.accountId };
  } catch (e) { throw e instanceof WalletError ? e : wrap(e); }
}

/** Have the wallet approve and sign a transfer Helm built. TRON returns the signed transaction (Helm broadcasts it);
 * Arc and NEAR wallets broadcast themselves and return the transaction hash. */
export async function signTransfer(net: NetInfo, userId: string, from: string, request: any): Promise<{ signed_tx?: any; tx_hash?: string }> {
  if (net.family === "tron") {
    const tw = tronWeb();
    if (tw?.defaultAddress?.base58 !== from) throw new WalletError(`Switch TronLink to ${from} first.`);
    if (tronNetworkOf(tw) !== net.id) throw new WalletError(`Switch TronLink to ${net.name} first.`);
    try { return { signed_tx: await tw.trx.sign(request.tx) }; } catch (e) { throw wrap(e); }
  }
  if (net.family === "evm") {
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
  const { wallet, accountId } = await nearAccount(net, userId);
  if (accountId !== from) throw new WalletError(`Switch the NEAR wallet to ${from} first.`);
  try {
    const out = await wallet.signAndSendTransaction({ signerId: from, receiverId: request.receiver_id, actions: request.actions });
    const tx_hash = out?.transaction?.hash ?? out?.transaction_outcome?.id;
    if (!tx_hash) throw new WalletError("The wallet didn't return the transaction; check its history before sending again.", false, false);
    return { tx_hash };
  } catch (e) { throw e instanceof WalletError ? e : wrap(e, true); }
}

/** Forget the NEAR Connect session in this browser (TronLink and EVM wallets are disconnected in the wallet). */
export async function forgetWallet(net: NetInfo, userId: string) {
  if (net.family !== "near") return;
  const c = await near(net, userId);
  await c.disconnect().catch(() => undefined);
}

export const WALLET_APP: Record<string, string> = { tron: "TronLink", evm: "a browser EVM wallet", near: "a NEAR wallet", ton: "a TON wallet" };
