"""The public pricing contract. Historical configuration cannot silently change it."""

LAUNCH_USD = 0
TRADE_BPS = 250
CREATOR_SHARE_BPS = 250
REFERRAL_BPS = 3000
POLICY_VERSION = "2026-11-free-launch-trade2.5-rewards2.5"


def public_pricing() -> dict:
    return {"version": POLICY_VERSION, "launch_usd": LAUNCH_USD, "trade_bps": TRADE_BPS,
            "creator_share_bps": CREATOR_SHARE_BPS, "route_fee_bps": 0, "referral_share_bps": REFERRAL_BPS,
            "admin_launches_free": True, "launch_exemptions_available": False,
            "launch_payment": "none",
            "launch_credit": "No launch payment required",
            "pro_infrastructure": "planned_optional", "third_party_costs": "Network gas and launchpad fees are separate"}
