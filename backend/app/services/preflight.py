"""Preflight: every spend is described first, and must be confirmed with a hash of exactly that description."""

import hashlib
import json


def preflight_hash(payload: dict) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def platform_fee_lamports(proceeds_lamports: int, bps: int) -> int:
    if bps <= 0 or proceeds_lamports <= 0:
        return 0
    return proceeds_lamports * bps // 10_000


def build_preflight(kind: str, summary: dict, warnings: list[str] | None = None) -> dict:
    """`summary` must contain everything that defines the action (wallets, amounts, fees)."""
    body = {"kind": kind, **summary}
    return {"preflight": {**body, "warnings": warnings or []}, "preflight_hash": preflight_hash(body)}
