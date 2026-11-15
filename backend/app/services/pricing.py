"""The public pricing contract. Historical configuration cannot silently change it."""

LAUNCH_USD = 20
TRADE_BPS = 100
REFERRAL_BPS = 3000
POLICY_VERSION = "2026-11-launch20-trade1"


def public_pricing() -> dict:
    return {"version": POLICY_VERSION, "launch_usd": LAUNCH_USD, "trade_bps": TRADE_BPS,
            "creator_share_bps": 0, "route_fee_bps": 0, "referral_share_bps": REFERRAL_BPS,
            "admin_launches_free": True, "launch_exemptions_available": True,
            "launch_payment": "stablecoin_where_supported_native_otherwise",
            "launch_credit": "Paid once per draft; confirmed payment is reused on deployment retry",
            "third_party_costs": "Network gas and launchpad fees are separate"}
