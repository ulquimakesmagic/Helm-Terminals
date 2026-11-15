export const BASE: string = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

// Memory-only bearer: reloads/new tabs require sign-in. No ambient authentication cookies.
let token: string | null = null;
try {
  sessionStorage.removeItem("helm_token");
} catch {
  token = null;
}

export function setToken(t: string | null) {
  token = t;
  try {
    sessionStorage.removeItem("helm_token");
  } catch {
    /* storage unavailable */
  }
}
export const hasToken = () => token !== null;

/** Owner-only media uses the same memory-only bearer, never a token in a URL. */
export async function performanceMedia(file?: File, remove = false): Promise<Blob | any> {
  const response = await fetch(BASE + "/profile/performance/media", {
    method: remove ? "DELETE" : file ? "PUT" : "GET",
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(file ? { "Content-Type": file.type || "application/octet-stream" } : {}) },
    body: file,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(response.status, error.detail || "Background request failed");
  }
  return file || remove ? response.json() : response.blob();
}

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
    if (res.status === 401 && token && ["missing bearer token", "invalid or expired session", "invalid session"].includes(detail)) {
      setToken(null);
      window.location.assign("/login");
    }
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const solscan = (sig: string) => `https://solscan.io/tx/${sig}`;
export const short = (a: string) => (a.length > 12 ? `${a.slice(0, 4)}…${a.slice(-4)}` : a);
export const fmt = (n: number, d = 4) => (Number.isFinite(n) ? n : 0).toLocaleString(undefined, { maximumFractionDigits: d });
/** Compact large counts (token holdings): 1,234,567 → 1.23M. Small numbers keep full precision. */
export const compact = (n: number, d = 2) => {
  const v = Number.isFinite(n) ? n : 0;
  return Math.abs(v) < 100_000 ? fmt(v, d) : v.toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 2 });
};
/** A signed amount, e.g. +1.25 / −0.4, using a true minus sign. */
export const signed = (n: number, d = 4) => {
  const a = Math.abs(Number.isFinite(n) ? n : 0);
  // A non-zero amount never shows as "0": widen the precision until its first digit appears (up to 9 places).
  const places = a > 0 && a < 10 ** -d ? Math.min(9, Math.ceil(-Math.log10(a)) + 1) : d;
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${fmt(a, places)}`;
};
export const sol = (n: number, d = 4) => `${fmt(n, d)} SOL`;
export const usd = (n: number) => `$${(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const errMsg = (e: unknown) => (e as ApiError)?.detail ?? String(e);
