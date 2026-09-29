import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, hasToken } from "./api";

export interface Me {
  id: string; email: string; totp_enabled: boolean; created_at: string;
  display_name?: string; handle?: string | null; avatar_hue?: number;
  /** Only decides whether the Admin link shows; the server checks the role on every admin request. */
  admin_role?: "superadmin" | "viewer" | null;
}
export interface Health { ok: boolean }
export interface TgStatus { linked: boolean; linked_at: string | null; bot_username: string; enabled: boolean }

interface Ctx { me: Me | null; health: Health | null; healthChecked: boolean; testSigner: boolean | null; tg: TgStatus | null; refresh: () => void }
const SessionCtx = createContext<Ctx>({ me: null, health: null, healthChecked: false, testSigner: null, tg: null, refresh: () => undefined });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthChecked, setHealthChecked] = useState(false);
  const [testSigner, setTestSigner] = useState<boolean | null>(null);
  const [tg, setTg] = useState<TgStatus | null>(null);
  const refresh = useCallback(() => {
    api<Health>("/health").then(setHealth).catch(() => setHealth(null)).finally(() => setHealthChecked(true));
    api<{ test_signer: boolean }>("/public-info").then((info) => setTestSigner(info.test_signer)).catch(() => setTestSigner(null));
    if (!hasToken()) return;
    api<Me>("/auth/me").then(setMe).catch(() => undefined);
    api<TgStatus>("/telegram/status").then(setTg).catch(() => undefined);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return <SessionCtx.Provider value={{ me, health, healthChecked, testSigner, tg, refresh }}>{children}</SessionCtx.Provider>;
}
export const useSession = () => useContext(SessionCtx);
