"""Telegram HTML rendering. Everything dynamic is escaped: token names and labels are user-controlled."""

from html import escape

MAX_LIST = 12
# Explorer links per network: (transaction prefix, token prefix). Unknown networks get the hash without a link.
EXPLORERS = {
    "solana": ("https://solscan.io/tx/", "https://solscan.io/token/"),
    "base": ("https://basescan.org/tx/", "https://basescan.org/token/"),
    "bsc": ("https://bscscan.com/tx/", "https://bscscan.com/token/"),
    "ethereum": ("https://etherscan.io/tx/", "https://etherscan.io/token/"),
    "robinhood": ("https://robinhoodchain.blockscout.com/tx/", "https://robinhoodchain.blockscout.com/token/"),
    "tron": ("https://tronscan.org/transaction/", "https://tronscan.org/token20/"),
    "near": ("https://nearblocks.io/txns/", "https://nearblocks.io/token/"),
    "arc": ("https://explorer.arc.io/tx/", "https://explorer.arc.io/token/"),
    "ton": ("https://tonviewer.com/transaction/", "https://tonviewer.com/"),
}


def _link(network: str | None, kind: int, value: str) -> str:
    """An explorer link for a transaction (kind 0) or token (kind 1), or just the short value when unknown."""
    prefix = EXPLORERS.get(network or "", (None, None))[kind]
    if not prefix:
        return f"<code>{e(short(value))}</code>"
    return f"<a href=\"{prefix}{e(value)}\">{e(short(value))}</a>"


def e(v: object) -> str:
    return escape(str(v), quote=False)


def short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}" if len(addr) > 12 else addr


def sid(item_id: str) -> str:
    return item_id[:6]


def num(v: float, d: int = 4) -> str:
    s = f"{v:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def render_wallets(wallets: list[dict]) -> str:
    if not wallets:
        return "No wallets yet. Create some in the web app."
    lines = ["<b>Your wallets</b>"]
    for w in wallets[:MAX_LIST * 3]:
        bal = f"  {num(w['balance_sol'])} SOL" if "balance_sol" in w else ""
        kind = " (watch-only)" if w["kind"] == "watch" else ""
        lines.append(f"<code>{sid(w['id'])}</code>  {e(w['label'] or 'wallet')}{kind}\n      <code>{e(short(w['address']))}</code>{bal}")
    return "\n".join(lines)


def render_launches(launches: list[dict]) -> str:
    if not launches:
        return "No launches yet. Create one in the web app."
    lines = ["<b>Your launches</b>"]
    for l in launches[:MAX_LIST * 3]:
        lines.append(f"<code>{sid(l['id'])}</code>  {e(l['name'])} <b>${e(l['symbol'])}</b>  [{e(l['status'])}]")
    return "\n".join(lines)


def _transparency(t: dict) -> list[str]:
    lines = ["", "<b>Supply transparency</b>",
             f"Your wallets hold <b>{num(t['controlled_pct'], 2)}%</b> of supply"]
    lines += [f"⚠️ {e(w)}" for w in t.get("warnings", [])]
    holders = t.get("top_holders", [])
    if holders:
        lines.append("Top holders:")
        for h in holders[:5]:
            tag = "  ← yours" if h["is_yours"] else ""
            lines.append(f"  <code>{e(short(h['owner']))}</code>  {num(h['pct'], 2)}%{tag}")
    return lines


def render_workspace(ws: dict, section: str = "all") -> str:
    l = ws["launch"]
    head = f"<b>{e(l['name'])}</b> ${e(l['symbol'])}  [{e(l['status'])}]"
    if l.get("mint_address"):
        head += "\n" + _link(l.get("network") or "solana", 1, l["mint_address"])
    lines = [head]
    if ws.get("rpc_error"):
        lines.append(f"⚠️ Chain data partly unavailable: {e(ws['rpc_error'])}")
    if section in ("all", "wallets"):
        lines += ["", "<b>Wallets</b>"] if ws["wallets"] else ["", "No wallets attached yet (use the web app)."]
        for w in ws["wallets"][:MAX_LIST]:
            lines.append(f"<code>{sid(w['id'])}</code> {e(w['label'] or short(w['address']))} [{e(w['role'])}]  "
                         f"{num(w['sol'])} SOL · {num(w['tokens'], 0)} tokens")
    if ws.get("transparency"):
        lines += _transparency(ws["transparency"])
    if section == "all":
        lines += ["", f"Net SOL flow: <b>{num(ws['pnl']['net_sol_flow'])} SOL</b> "
                      f"({ws['pnl']['confirmed_txs']} confirmed txs)"]
        for t in ws["transactions"][:5]:
            lines.append(f"  {e(t['kind'])} · {e(t['status'])} · {num(t['sol_delta'])} SOL")
    return "\n".join(lines)


def _value(v: object) -> str:
    if isinstance(v, dict):  # e.g. a deploy's USDC funding step: one line per detail, internal ids left out
        return "\n" + "\n".join(f"  • {e(k.replace('_', ' '))}: {_value(x)}" for k, x in v.items()
                                if not k.endswith("_id") and k != "amount_usd")
    if isinstance(v, list):
        items = [(", ".join(f"{k}: {x}" for k, x in i.items()) if isinstance(i, dict) else str(i)) for i in v]
        extra = f"\n  … and {len(items) - MAX_LIST} more" if len(items) > MAX_LIST else ""
        return "\n" + "\n".join(f"  • <code>{e(i)}</code>" for i in items[:MAX_LIST]) + extra
    if isinstance(v, str) and 32 <= len(v) <= 44 and " " not in v:
        return f"<code>{e(v)}</code>"
    return e(v)


def render_preflight(title: str, pf: dict, requires_2fa: bool) -> str:
    skip = {"kind", "warnings", "launch_id"}
    lines = [f"<b>{e(title)}</b>", "Review before confirming:", ""]
    for k, v in pf.items():
        if k in skip:
            continue
        lines.append(f"{e(k.replace('_', ' '))}: {_value(v)}")
    for w in pf.get("warnings", []):
        lines.append(f"\n⚠️ {e(w)}")
    if requires_2fa and not pf.get("warnings"):
        lines.append("\n🔐 This needs your 2FA code.")
    return "\n".join(lines)


def render_job(job: dict) -> str:
    icon = {"confirmed": "✅", "failed": "❌", "partial": "⚠️", "unconfirmed": "❓"}.get(job["status"], "⏳")
    lines = [f"{icon} <b>{e(job['kind'])}</b>: {e(job['status'])}"]
    if job.get("error"):
        lines.append(e(job["error"]))
    if job["status"] == "unconfirmed":
        lines.append("It may still land. Check the transaction on the explorer before trying again.")
    for t in job.get("transactions", [])[:MAX_LIST]:
        if t.get("signature"):
            lines.append(f"  {e(t['kind'])} · {e(t['status'])} · {_link(job.get('network'), 0, t['signature'])}")
        else:
            lines.append(f"  {e(t['kind'])} · {e(t['status'])} {e(t.get('error') or '')}")
    lines.append(f"\nJob <code>{e(job['id'])}</code>")
    return "\n".join(lines)
