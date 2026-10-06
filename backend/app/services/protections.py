"""Launch protections each integrated launchpad actually offers, for the launch form and Launchdesk.

Only settings a launchpad documents and Helm's integration passes through are marked available. Everything else is
listed as unavailable rather than imitated: Helm can't add per-wallet limits, allowlists or cooldowns to someone
else's contracts.
"""

GENERIC = [
    ("launch_phases", "Launch phases (e.g. allowlist first, then public)"),
    ("per_wallet_limit", "Per-wallet purchase limit"),
    ("allowlist", "Allowlist"),
    ("cooldown", "Cooldown between buys"),
]

SPECIFIC = {
    "clanker": [{
        "id": "sniper_fee", "label": "Descending sniper fee", "available": True, "configurable": True,
        "options": ["sniper_start_pct", "sniper_decay_s"],
        "effect": "The first swaps pay a high LP fee (you choose up to 80%) that falls to about 4.17% over the decay "
                  "time (up to 2 minutes), so bots that buy in the first block pay heavily.",
        "fee": "Paid by early buyers as LP fees to the reward recipients (you, plus Helm's reward share if set). "
               "No separate Helm fee.",
        "source": "Clanker v4 MEV module (ClankerMevDescendingFees)",
    }],
    "meteora-dbc": [{
        "id": "anti_sniping", "label": "Jupiter Studio anti-sniping", "available": True, "configurable": True,
        "options": ["anti_sniping"],
        "effect": "Jupiter Studio's anti-sniping setting for the pool: early buys pay a higher fee in the first "
                  "moments after launch.",
        "fee": "Set by Jupiter Studio and paid to the pool by early buyers. No Helm fee.",
        "source": "Jupiter Studio create-tx (antiSniping)",
    }],
}
SPECIFIC["clanker-robinhood"] = SPECIFIC["clanker"]
SPECIFIC["tolly"] = [{
    "id": "anti_snipe_max_wallet", "label": "Anti-snipe max wallet", "available": True, "configurable": False,
    "options": [],
    "effect": "For the first 5 minutes after launch no wallet may hold more than 3% of supply (Tolly's contract).",
    "fee": "None.",
    "source": "TollyPad.MAX_WALLET / antiSnipeSeconds (TollyLabs/v3-contracts)",
}]


def protections_for(launchpad: str) -> dict:
    specific = SPECIFIC.get(launchpad, [])
    items = list(specific) + [
        {"id": gid, "label": label, "available": False, "configurable": False,
         "effect": f"Not offered by {launchpad} through Helm's integration.", "fee": None, "source": None}
        for gid, label in GENERIC]
    return {"launchpad": launchpad, "items": items,
            "summary": ("Available: " + ", ".join(p["label"] for p in specific)) if specific else
                       f"{launchpad} offers no configurable anti-sniping or fair-launch settings through Helm."}
