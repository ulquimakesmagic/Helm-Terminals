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
    from . import platform_config as pc
    from .pricing import CREATOR_SHARE_BPS
    share = pc.creator_share(identifier)
    no_rewards = identifier in {"uniswap-v2", "four-meme", "sunpump", "meme-cooking", "tolly"}
    reward_ready = no_rewards or share["supported"] or identifier.startswith("flap-sh")
    return {"create": implemented and reward_ready, "buy": implemented, "sell": implemented,
            "claim_mode": claim, "automation": implemented and not extra,
            "supply_lock": False, "creator_reward_share_bps": CREATOR_SHARE_BPS,
            "creator_reward_collection": "not_applicable" if no_rewards else share["collection"],
            "creator_reward_collection_ready": reward_ready,
            "creator_reward_note": "No separate creator rewards in this launch mode" if no_rewards else share["note"],
            "trade_fee_bps": 250, "launch_fee_usd": 0,
            "wallet_mode": "managed", "verification": "implementation_tests_live_acceptance_required",
            "trade_semantics": "auction_deposit_withdrawal" if identifier == "meme-cooking" else "buy_sell",
            "configured": status == "available"}
