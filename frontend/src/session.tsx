import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, hasToken } from "./api";

export interface Me {
  id: string; email: string; totp_enabled: boolean; created_at: string;
  email_verified?: boolean; recovery_hold_until?: string | null;
  display_name?: string; handle?: string | null; avatar_hue?: number;
  /** Only decides whether the Admin link shows; the server checks the role on every admin request. */
  admin_role?: "superadmin" | "viewer" | null;
}
export interface Health { ok: boolean; checkedAt?: number }
export interface TgStatus { linked: boolean; linked_at: string | null; bot_username: string; enabled: boolean }
interface Capabilities { telegram: "read_only"; cash_routes: string[]; session_storage: "memory_only"; email_recovery: boolean }

interface Ctx { me: Me | null; health: Health | null; healthChecked: boolean; testSigner: boolean | null; tg: TgStatus | null; capabilities: Capabilities | null; refresh: () => void }
const SessionCtx = createContext<Ctx>({ me: null, health: null, healthChecked: false, testSigner: null, tg: null, capabilities: null, refresh: () => undefined });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthChecked, setHealthChecked] = useState(false);
  const [testSigner, setTestSigner] = useState<boolean | null>(null);
  const [tg, setTg] = useState<TgStatus | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const refresh = useCallback(() => {
    api<{ test_signer: boolean; capabilities: Capabilities }>("/public-info").then((info) => {setTestSigner(info.test_signer); setCapabilities(info.capabilities);}).catch(() => {setTestSigner(null); setCapabilities(null);});
    if (!hasToken()) return;
    api<Me>("/auth/me").then(setMe).catch(() => undefined);
    api<TgStatus>("/telegram/status").then(setTg).catch(() => undefined);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    let active = true, pending = false;
    const check = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const value = await api<Health>("/health");
        if (active) setHealth(value.ok ? {...value, checkedAt: Date.now()} : null);
      } catch { if (active) setHealth(null); }
      finally { pending = false; if (active) setHealthChecked(true); }
    };
    void check();
    const timer = setInterval(check, 30000);
    window.addEventListener("online", check);
    document.addEventListener("visibilitychange", check);
    return () => { active = false; clearInterval(timer); window.removeEventListener("online", check); document.removeEventListener("visibilitychange", check); };
  }, []);
  return <SessionCtx.Provider value={{ me, health, healthChecked, testSigner, tg, capabilities, refresh }}>{children}</SessionCtx.Provider>;
}
export const useSession = () => useContext(SessionCtx);
