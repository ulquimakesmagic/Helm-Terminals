"""Public launchpad capabilities shared by previews and the user interface.

Available means implemented/configured, not independently certified against live mainnet.
"""

CLAIMS = {"pumpfun": "in-app", "bags": "in-app", "meteora-dbc": "in-app",
          "clanker": "in-app", "clanker-robinhood": "in-app", "pons": "in-app",
          "letsbonk": "external", "printr": "external", "meme-cooking": "auction"}


def launchpad_capabilities(identifier, network, status):
    extra = network in {"tron", "near", "arc"}
    claim = "automatic" if identifier.startswith("flap-sh") else CLAIMS.get(identifier, "none")
    implemented = status != "planned"
    return {"create": implemented, "buy": implemented, "sell": implemented,
            "claim_mode": claim, "automation": implemented and not extra,
            "supply_lock": False, "creator_reward_share_bps": 0,
            "trade_fee_bps": 100, "launch_fee_usd": 20,
            "wallet_mode": "managed", "verification": "implementation_tests_live_acceptance_required",
            "trade_semantics": "auction_deposit_withdrawal" if identifier == "meme-cooking" else "buy_sell",
            "configured": status == "available"}
