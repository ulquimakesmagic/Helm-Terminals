const BASE: string = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

// Session token lives in sessionStorage: cleared when the tab closes. Use httpOnly cookies for a hardened deploy.
let token: string | null = null;
try {
  token = sessionStorage.getItem("helm_token");
} catch {
  token = null;
}

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem("helm_token", t);
    else sessionStorage.removeItem("helm_token");
  } catch {
    /* storage unavailable */
  }
}
export const hasToken = () => token !== null;

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method: opts.method ?? (opts.body ? "POST" : "GET"),
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Helm Terminals server. Check that the API is running and reachable.");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail)
        ? data.detail.map((d: any) => {
            const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : "";
            const msg = String(d.msg ?? "invalid value").replace(/^Value error, /, "");
            return field && field !== "body" ? `${String(field).replace(/_/g, " ")}: ${msg}` : msg;
          }).join(". ")
        : JSON.stringify(data.detail ?? data);
    if (res.status === 401 && token && !detail.startsWith("totp_required")) {
      setToken(null);
      window.location.assign("/login");
    }
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const solscan = (sig: string) => `https://solscan.io/tx/${sig}`;
export const short = (a: string) => (a.length > 12 ? `${a.slice(0, 4)}…${a.slice(-4)}` : a);
export const fmt = (n: number, d = 4) => n.toLocaleString(undefined, { maximumFractionDigits: d });
export const sol = (n: number, d = 4) => `${fmt(n, d)} SOL`;
export const usd = (n: number) => `$${(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const errMsg = (e: unknown) => (e as ApiError)?.detail ?? String(e);
