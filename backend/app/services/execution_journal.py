"""Persist operation boundaries separately from the transactions that happen to exist.

An incomplete dispatch can never be inferred complete from a successful prefix.
This journal deliberately does not automatically replay interrupted financial work.
"""
from collections import Counter


def initialize(params: dict) -> dict:
    return {**params, "execution": {"version": 1, "dispatch_complete": False, "required": []}}


async def require_steps(db, job, wallet_id: str, kinds: list[str]) -> None:
    """Record all steps of a prepared plan before its first signature."""
    journal = (job.params or {}).get("execution") or initialize({})["execution"]
    if journal.get("dispatch_complete"):
        raise RuntimeError("cannot append work to a sealed execution")
    required = [*journal.get("required", []), *({"wallet_id": wallet_id, "kind": k} for k in kinds)]
    job.params = {**(job.params or {}), "execution": {**journal, "required": required}}
    await db.commit()


def seal(job, results) -> None:
    """The orchestrator reached its end, including failed/uncertain outcomes.

Result hashes provide an additional completion witness, not permission to replay.
No signature means no chain evidence; that result cannot be recovered as success.
"""
    journal = (job.params or {}).get("execution") or initialize({})["execution"]
    job.params = {**(job.params or {}), "execution": {
        **journal, "dispatch_complete": True,
        "outcomes": [{"signature": r.signature, "status": r.status} for r in results],
    }}


def can_resolve(job, transactions) -> bool:
    """Only an explicitly finished dispatch with all required evidence can resolve."""
    journal = (job.params or {}).get("execution") or {}
    if journal.get("version") != 1 or not journal.get("dispatch_complete"):
        return False  # old jobs and crashed dispatches require investigation
    outcomes = journal.get("outcomes") or []
    if not outcomes or any(not r.get("signature") for r in outcomes):
        return False
    seen = {t.signature for t in transactions if t.signature}
    if any(r["signature"] not in seen for r in outcomes):
        return False
    required = Counter((r["wallet_id"], r["kind"]) for r in journal.get("required", []))
    actual = Counter((t.wallet_id, t.kind) for t in transactions)
    if required - actual:
        return False
    # A source receipt cannot prove that a destination fill arrived.
    from ..chains.relay import DONE
    params = job.params or {}
    if params.get("required_followup_pending") or params.get('gas_funding_followup_pending'):
        return False
    if params.get('provider_plan_business_pending'):
        return False
    if job.kind == "bridge" and params.get("route_status") not in DONE:
        return False
    funding = params.get("funding") or {}
    if funding.get("request_id") and funding.get("route_status") not in DONE:
        return False
    return True
