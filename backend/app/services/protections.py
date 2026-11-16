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
        "id": "sniper_fee", "label": "Factory anti-sniping", "available": True, "configurable": True,
        "options": ["anti_sniping"],
        "effect": "Enable or disable V5 launch anti-sniping. The live factory determines the opening total fee and decay window; review them before signing.",
        "fee": "The extra anti-sniping fee goes to Clanker. Normal tax, creator rewards and Helm trading fees are separate.",
        "source": "Clanker V5 factory taxPolicy / TaxLaunchHook",
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
SPECIFIC["clanker-bsc"] = SPECIFIC["clanker"]
SPECIFIC["tolly"] = [{
    "id": "anti_snipe_max_wallet", "label": "Anti-snipe max wallet", "available": True, "configurable": False,
    "options": [],
    "effect": "For the first 5 minutes after launch no wallet may hold more than 3% of supply (Tolly's contract).",
    "fee": "None.",
    "source": "TollyPad.MAX_WALLET / antiSnipeSeconds (TollyLabs/v3-contracts)",
}]
SPECIFIC["meme-cooking"] = [{
    "id": "fair_auction", "label": "Fair-launch auction", "available": True, "configurable": True,
    "options": ["duration_h", "soft_cap_near", "hard_cap_near"],
    "effect": "Everyone deposits during the countdown and gets the same price; withdrawing early costs 2%, which "
              "goes to those who stay. Below the soft cap everyone is refunded.",
    "fee": "Meme.Cooking's own deposit and withdrawal fees. No Helm fee.",
    "source": "docs.meme.cooking (How it works)",
}]


def protections_for(launchpad: str, version: int | None = None) -> dict:
    specific = SPECIFIC.get(launchpad, [])
    if launchpad.startswith('clanker') and version==4:
        specific=[{'id':'sniper_fee','label':'Historical V4 sniper fee','available':True,'configurable':False,
            'options':['sniper_start_pct','sniper_decay_s'],
            'effect':'This token keeps its original V4 descending sniper fee and decay schedule. V5 factory policy does not change it.',
            'fee':'Historical V4 LP fees follow the original on-chain reward recipients.',
            'source':'Clanker V4 launch configuration'}]
    items = list(specific) + [
        {"id": gid, "label": label, "available": False, "configurable": False,
         "effect": f"Not offered by {launchpad} through Helm's integration.", "fee": None, "source": None}
        for gid, label in GENERIC]
    return {"launchpad": launchpad, "items": items,
            "summary": ("Available: " + ", ".join(p["label"] for p in specific)) if specific else
                       f"{launchpad} offers no configurable anti-sniping or fair-launch settings through Helm."}
