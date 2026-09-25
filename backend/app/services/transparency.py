"""Supply Transparency: show how much of a token the user's own wallets control, next to real holders."""


def compute_transparency(
    total_supply: int,
    decimals: int,
    controlled: dict[str, int],  # user wallet address -> raw token amount
    top_holders: list[tuple[str, int]],  # (owner, raw amount) from the chain
    warn_pct: float,
) -> dict:
    controlled_total = sum(controlled.values())
    pct = (controlled_total / total_supply * 100) if total_supply else 0.0
    mine = set(controlled)
    holders = [
        {
            "owner": owner,
            "amount": amount / 10**decimals,
            "pct": (amount / total_supply * 100) if total_supply else 0.0,
            "is_yours": owner in mine,
        }
        for owner, amount in top_holders
    ]
    warnings = []
    if pct > warn_pct:
        warnings.append(
            f"Your wallets hold {pct:.1f}% of supply, above the {warn_pct:.0f}% transparency threshold. "
            "Disclose this to your community."
        )
    return {
        "total_supply": total_supply / 10**decimals,
        "controlled_supply": controlled_total / 10**decimals,
        "controlled_pct": pct,
        "wallets": [
            {"address": a, "amount": v / 10**decimals, "pct": (v / total_supply * 100) if total_supply else 0.0}
            for a, v in controlled.items()
        ],
        "top_holders": holders,
        "warnings": warnings,
    }
