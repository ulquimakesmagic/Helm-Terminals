"""Reviewed platform payments, exact fee splits and restart-safe payment credits.

The treasury receives the gross fee. Thirty percent is a separately reported liability
to the referrer, never Helm revenue. No quote alone is recorded as received income.
"""
from decimal import Decimal, ROUND_CEILING
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import LaunchFeeExemption, PlatformCharge, ReferralLink, RevenueEvent, User, Wallet, utcnow
from . import platform_config as pc, quote_approval
from .pricing import LAUNCH_USD, POLICY_VERSION, REFERRAL_BPS, TRADE_BPS


async def exemption(db, user) -> str | None:
    from .admin import role_of
    if await role_of(db, user):
        return "Admin account"
    row = await db.get(LaunchFeeExemption, user.id)
    return "Account launch-fee exemption" if row and row.enabled else None


def split_fee(amount: int, referred: bool) -> tuple[int, int]:
    if amount < 0:
        raise ValueError("A fee cannot be negative")
    referral = amount * REFERRAL_BPS // 10_000 if referred else 0
    return amount - referral, referral


async def native_balance(ex, network, address):
    if network == "solana":
        return await ex.rpc.get_balance(address)
    if network in {"tron", "near"}:
        from .extra_chains import native_balance as balance
        return await balance(ex, network, address)
    return await ex.evm(network).rpc.get_balance(address)


def native_asset(network):
    from .chain_balances import native_coin
    return native_coin(network), {"solana": 9, "tron": 6, "near": 24}.get(network, 18)


async def launch_quote(db, user, ex, launch, wallet, approval=None):
    """Return frozen, signed terms; confirmation reuses them instead of repricing."""
    await pc.refresh(db)
    waived = await exemption(db, user)
    old = (await db.execute(select(PlatformCharge).where(
        PlatformCharge.reference_key == f"launch:{launch.id}"))).scalar_one_or_none()
    if old and old.status == "confirmed":
        waived = "Launch fee already paid for this draft"
    if old and old.signature and old.status not in {"confirmed", "failed"}:
        raise HTTPException(409, "The existing launch payment is unconfirmed. Reconcile it before trying again.")
    scope = {"purpose": "launch_fee", "launch_id": launch.id, "network": launch.network}
    if waived:
        return {"usd": 0, "standard_usd": LAUNCH_USD, "exempt": True, "reason": waived,
                "creator_rewards": "100% to creator", "policy": POLICY_VERSION}, None, []
    destination = pc.treasury_for(launch.network)
    from ..deps import valid_address_for
    warnings = []
    if not destination or not valid_address_for(launch.network, destination):
        warnings.append("The launch-fee treasury is not configured for this network. Contact support.")
    if approval:
        terms = quote_approval.verify(approval, user.id, scope)
        if terms.get("destination") != destination or terms.get("policy") != POLICY_VERSION:
            raise HTTPException(409, "Launch fee terms changed; request a new preview")
        source = await db.get(Wallet, terms.get("wallet_id"))
        if not source or source.user_id != user.id or source.kind != "managed" or source.network != launch.network:
            raise HTTPException(409, "The launch-fee wallet changed; request a new preview")
        return terms, approval, warnings
    from .chain_balances import STABLES, STABLE_DECIMALS
    symbol, decimals = native_asset(launch.network)
    token = None
    if launch.network in STABLES:
        symbol, token = STABLES[launch.network]
        decimals = STABLE_DECIMALS[launch.network]
        raw = LAUNCH_USD * 10 ** decimals
    elif launch.network == "arc":
        raw = LAUNCH_USD * 10 ** decimals  # Arc's native gas asset is USDC, represented once.
    else:
        from .portfolio import coin_prices
        price = (await coin_prices()).get(symbol)
        if not price or not Decimal(str(price)).is_finite() or price <= 0:
            raise HTTPException(503, "A fresh native-coin USD price is unavailable; no launch fee was quoted")
        raw = int((Decimal(LAUNCH_USD) * 10 ** decimals / Decimal(str(price))).to_integral_value(rounding=ROUND_CEILING))
    source = wallet
    if token:
        from .chain_balances import token_balance_raw
        candidates = [wallet, *(await db.execute(select(Wallet).where(
            Wallet.user_id == user.id, Wallet.network == launch.network,
            Wallet.kind == "managed", Wallet.id != wallet.id).order_by(Wallet.created_at))).scalars().all()]
        found = False
        for candidate in candidates[:100]:
            try:
                if await token_balance_raw(ex, launch.network, token, candidate.address) >= raw:
                    source, found = candidate, True
                    break
            except Exception:
                continue
        if not found:
            warnings.append(f"Keep {LAUNCH_USD} {symbol} in a managed {launch.network} wallet for the launch fee.")
    elif await native_balance(ex, launch.network, source.address) < raw:
        warnings.append(f"The launch-fee wallet needs {Decimal(raw) / 10 ** decimals:f} {symbol} plus network fees.")
    # A reserve is disclosed separately from Helm's fee. Deployment costs are reviewed by the adapter.
    reserve = None
    if launch.network == "solana":
        reserve = 10_000_000  # conservative SOL network fee and associated-account rent headroom
    elif launch.network not in {"tron", "near"}:
        reserve = await ex.evm(launch.network).max_gas_cost(100_000)
    if reserve is not None and await native_balance(ex, launch.network, source.address) < reserve + (raw if not token else 0):
        warnings.append("The launch-fee wallet needs native gas in addition to the reviewed launch payment.")
    terms = {"usd": LAUNCH_USD, "exempt": False, "policy": POLICY_VERSION, "network": launch.network,
             "wallet_id": source.id, "payer": source.address, "destination": destination,
             "asset": symbol, "token": token, "decimals": decimals, "amount_raw": str(raw),
             "amount": format(Decimal(raw) / 10 ** decimals, "f"), "creator_rewards": "100% to creator",
             "network_fee_reserve_raw": str(reserve) if reserve is not None else None,
             "network_fee_note": "Network gas and account rent are separate costs; unused reserve stays in your wallet.",
             "payment_terms": "Paid before deployment. Confirmed payment stays credited to this draft if deployment fails; retries do not pay again."}
    token = quote_approval.issue(user.id, scope, terms)
    return quote_approval.verify(token, user.id, scope), token, warnings


async def attach_launch_fee(pf, db, user, ex, launch, wallet, body):
    terms, approval, warnings = await launch_quote(db, user, ex, launch, wallet, body.launch_fee_approval)
    if body.confirm and not terms["exempt"] and not body.launch_fee_approval:
        raise HTTPException(409, "Review a fresh launch-fee quote before confirming")
    from .preflight import build_preflight
    summary = {k: v for k, v in pf["preflight"].items() if k not in {"kind", "warnings"}}
    summary.update(launch_fee_payment=terms, helm_fees="Launch fee waived" if terms["exempt"] else "$20 launch fee; no creator-reward share",
                   platform_fee="Launch fee waived" if terms["exempt"] else "$20 launch fee")
    if launch.dev_buy_sol > 0:
        agreed = await trade_terms(db, launch.network)
        symbol, decimals = native_asset(launch.network)
        basis = int(Decimal(str(launch.dev_buy_sol)) * 10 ** decimals)
        summary.update(trade_fee_terms=agreed, initial_buy_fee={"wallet_id": wallet.id,
            "basis_raw": str(basis), "amount_raw": str(basis * TRADE_BPS // 10_000),
            "asset": symbol, "decimals": decimals, "destination": agreed["destination"],
            "policy": POLICY_VERSION, "network": launch.network,
            "note": "1% of the initial developer buy, due only when deployment and that buy confirm. Launch exemptions do not waive trading fees."})
        summary["helm_fees"] += "; 1% of the initial developer buy"
        summary["platform_fee"] += "; 1% initial-buy trading fee"
    result = build_preflight("deploy", summary, [*pf["preflight"].get("warnings", []), *warnings])
    result["launch_fee_approval"] = approval
    return result


async def _send(ex, wallet, terms, persist):
    from .executor import SubmitResult
    raw = int(terms["amount_raw"])
    network, destination, token = wallet.network, terms["destination"], terms.get("token")
    if network == "solana":
        from solders.pubkey import Pubkey
        from ..chains.solana import build_unsigned, create_ata_idempotent_ix, sol_transfer_ix, token_transfer_checked_ix
        owner, target = Pubkey.from_string(wallet.address), Pubkey.from_string(destination)
        program = await ex.rpc.get_mint_program(token) if token else None
        ixs = ([create_ata_idempotent_ix(owner, target, Pubkey.from_string(token), program),
                token_transfer_checked_ix(owner, target, Pubkey.from_string(token), raw, terms["decimals"], program)]
               if token else [sol_transfer_ix(owner, target, raw)])
        if terms.get("payment_reference"):
            from solders.instruction import Instruction
            # Distinct payments in one block must not share a deterministic Solana signature.
            ixs.append(Instruction(Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
                                   f"Helm payment {terms['payment_reference']}".encode(), []))
        unsigned = build_unsigned(owner, ixs, await ex.rpc.get_latest_blockhash())
        return await ex.submit(unsigned, {wallet.address: wallet.signer_ref or ""}, on_signed=persist)
    if network == "tron":
        from ..chains import tron
        from .extra_chains import tron_client
        client = tron_client(network)
        tx = await client.build_trx_transfer(wallet.address, destination, raw)
        tron.check_transaction(tx, owner=wallet.address, to=destination, amount=raw)
        h, state, error, _ = await tron.sign_and_send(client, ex.signer, wallet.signer_ref, tx, on_signed=persist)
        return SubmitResult(h, state, error)
    if network == "near":
        from ..chains import near
        from solders.pubkey import Pubkey
        from .extra_chains import near_client
        pubkey = None
        # The signer interface is authoritative for the key; a NEAR implicit account encodes its public key.
        if len(wallet.address) == 64:
            pubkey = bytes.fromhex(wallet.address)
        if pubkey is None:
            raise ValueError("Named NEAR fee wallets require a verified public-key reference")
        h, state, error = await near.sign_and_send(near_client(network), ex.signer, wallet.signer_ref,
            wallet.address, pubkey, destination, [near.transfer_action(raw)], on_signed=persist)
        return SubmitResult(h, state, error)
    from .evm_exec import EvmCall
    from .execution_intent import AmountBound, ExecutionIntent
    from .tx_guard import TRANSFER
    from eth_abi import encode
    import time
    call = EvmCall(token, TRANSFER + encode(["address", "uint256"], [destination, raw])) if token else EvmCall(destination, value=raw)
    asset = token.lower() if token else "native"
    intent = ExecutionIntent(network, wallet.address.lower(), frozenset({destination.lower()}), frozenset({asset}),
        (AmountBound(asset, raw),), (), (), (), int(time.time()) + 120)
    return await ex.evm(network).send(wallet.address, wallet.signer_ref or "", call, on_signed=persist, intent=intent)


async def collect(db, ex, job, wallet, kind, terms, reference_key):
    """Claim the payment key before signing; an uncertain signed payment is never replayed."""
    from .executor import SubmitResult, _record
    from ..deps import valid_address_for
    if (not valid_address_for(wallet.network, terms.get("destination", ""))
            or terms["destination"].lower() == wallet.address.lower()):
        return SubmitResult(None, "failed", "The fee recipient is invalid or is the paying wallet")
    row = (await db.execute(select(PlatformCharge).where(PlatformCharge.reference_key == reference_key)
                            .with_for_update())).scalar_one_or_none()
    if row and row.status == "confirmed":
        return SubmitResult(row.signature, "confirmed")
    if row and row.status != "failed" and (row.signature or row.status in {"pending", "processing"}):
        return SubmitResult(row.signature, "timeout", "Existing payment needs reconciliation; it was not repeated")
    if kind == "launch" and terms.get("expires", float("inf")) <= time.time():
        return SubmitResult(None, "failed", "The approved launch payment expired; review a fresh preview")
    if row and row.status == "failed":
        row.reference_key = f"{reference_key}:failed:{row.id}"
        row.status = "superseded"
        await db.flush()  # Rotate the failed attempt and create its replacement in one transaction.
        row = None
    if row is None:
        link = (await db.execute(select(ReferralLink).where(ReferralLink.referred_user_id == job.user_id))).scalar_one_or_none()
        platform, referral = split_fee(int(terms["amount_raw"]), bool(link and link.referrer_user_id != job.user_id))
        row = PlatformCharge(reference_key=reference_key, job_id=job.id, launch_id=job.launch_id, wallet_id=wallet.id,
            payer_user_id=job.user_id, kind=kind, network=wallet.network, asset=terms["asset"], token=terms.get("token"),
            decimals=terms["decimals"], amount_raw=int(terms["amount_raw"]), platform_raw=platform, referral_raw=referral,
            referrer_user_id=link.referrer_user_id if referral else None, referral_status="owed" if referral else "none",
            status="processing", destination=terms["destination"], terms=terms)
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return SubmitResult(None, "timeout", "A concurrent payment owns this launch credit; reconcile before retrying")
    else:
        row.status, row.job_id, row.terms = "processing", job.id, terms
        await db.commit()
    charge_id = row.id
    tx_kind = "launch_fee" if kind == "launch" else "trade_fee"
    from .execution_journal import require_steps
    await require_steps(db, job, wallet.id, [tx_kind])

    async def persist(signature):
        from ..db import get_sessionmaker
        from .executor import _persist_pending_transaction
        await _persist_pending_transaction(job, wallet, tx_kind, signature)
        async with get_sessionmaker()() as pending:
            charge = await pending.get(PlatformCharge, charge_id)
            charge.signature, charge.status = signature, "pending"
            event = (await pending.execute(select(RevenueEvent).where(RevenueEvent.reference == f"charge:{charge.id}"))).scalar_one_or_none()
            if event is None:
                pending.add(RevenueEvent(source="launch_fee" if kind == "launch" else "trade_fee",
                    network=wallet.network, asset=charge.asset, amount_raw=charge.platform_raw, decimals=charge.decimals,
                    estimated=False, status="pending", destination=charge.destination, launch_id=job.launch_id,
                    job_id=job.id, payer_user_id=job.user_id, reference=f"charge:{charge.id}",
                    note="Exact platform payment; referral liability excluded from Helm revenue"))
            await pending.commit()
    try:
        result = await _send(ex, wallet, {**terms, "payment_reference": charge_id}, persist)
    except Exception as exc:
        await db.refresh(row)
        result = SubmitResult(row.signature, "timeout" if row.signature else "failed", f"Payment could not be completed ({type(exc).__name__})")
    await db.refresh(row)
    row.status = "confirmed" if result.status == "confirmed" else "failed" if result.status == "failed" else "pending"
    row.signature = row.signature or result.signature
    event = (await db.execute(select(RevenueEvent).where(RevenueEvent.reference == f"charge:{row.id}"))).scalar_one_or_none()
    if event:
        event.status = "received" if row.status == "confirmed" else row.status
    await _record(db, job, wallet, tx_kind, result, None)
    await db.commit()
    return result


async def pay_launch(db, ex, job, launch):
    terms = (job.params or {}).get("launch_fee_payment") or {}
    if terms.get("exempt"):
        return True
    if not terms or terms.get("policy") != POLICY_VERSION:
        job.status, job.params = "failed", {**job.params, "error": "A reviewed launch payment is required"}
        launch.status = "failed"
        await db.commit()
        return False
    wallet = await db.get(Wallet, terms["wallet_id"])
    if not wallet or wallet.user_id != job.user_id or wallet.kind != "managed" or wallet.network != launch.network:
        raise ValueError("Launch-fee source no longer matches the confirmed wallet")
    result = await collect(db, ex, job, wallet, "launch", terms, f"launch:{launch.id}")
    if result.status != "confirmed":
        job.status = "unconfirmed" if result.status == "timeout" else "failed"
        job.params = {**job.params, "error": result.error or "Launch fee did not confirm; no deployment was attempted"}
        launch.status = "deploying" if result.status == "timeout" else "failed"
        await db.commit()
        return False
    return True


async def charge_trade(db, ex, job, wallet, basis_raw, *, token=None, asset=None, decimals=None):
    """Collect 1% of a confirmed trade's actual input/output basis. A failed fee remains visible."""
    if basis_raw <= 0:
        from .executor import SubmitResult
        return SubmitResult(None, "failed", "The trade's fee basis could not be verified")
    await pc.refresh(db)
    symbol, native_decimals = native_asset(wallet.network)
    agreed = (job.params or {}).get("trade_fee_terms") or {}
    destination = agreed.get("destination") or pc.treasury_for(wallet.network)
    from ..deps import valid_address_for
    from .executor import SubmitResult
    if not destination or not valid_address_for(wallet.network, destination):
        return SubmitResult(None, "failed", "The trading-fee treasury is not configured")
    raw = basis_raw * TRADE_BPS // 10_000
    if raw == 0:
        return SubmitResult(None, "confirmed")  # sub-base-unit fees round down; no fictional income.
    terms = {"network": wallet.network, "destination": destination, "amount_raw": str(raw),
             "asset": asset or symbol, "token": token, "decimals": decimals if decimals is not None else native_decimals,
             "basis_raw": str(basis_raw), "bps": TRADE_BPS, "policy": POLICY_VERSION}
    return await collect(db, ex, job, wallet, "trade", terms, f"trade:{job.id}:{wallet.id}")


async def trade_terms(db, network):
    await pc.refresh(db)
    from ..deps import valid_address_for
    destination = pc.treasury_for(network)
    if not destination or not valid_address_for(network, destination):
        raise HTTPException(503, "The 1% trading-fee treasury is not configured for this network")
    return {"bps": TRADE_BPS, "destination": destination, "referral_share_bps": REFERRAL_BPS,
            "network": network, "policy": POLICY_VERSION}


async def initial_buy_fee(db, ex, job, launch, wallet, *, recovery=False):
    """Settle the reviewed initial buy after a live deployment; recovery only records debt, never signs."""
    terms = (job.params or {}).get("initial_buy_fee")
    if not terms or launch.status != "live" or terms.get("policy") != POLICY_VERSION:
        return None
    if (wallet.id != terms.get("wallet_id") or wallet.user_id != job.user_id
            or wallet.kind != "managed" or wallet.network != launch.network):
        raise ValueError("Initial-buy fee source no longer matches the reviewed managed wallet")
    if int(terms["amount_raw"]) == 0:
        return None
    from .executor import SubmitResult, _finish
    reference = f"trade:{job.id}:{wallet.id}"
    existing = (await db.execute(select(PlatformCharge).where(PlatformCharge.reference_key == reference))).scalar_one_or_none()
    if existing:
        result = SubmitResult(existing.signature, "confirmed" if existing.status == "confirmed" else
                              "failed" if existing.status == "failed" else "timeout")
    elif recovery:
        # After a crash there is no permission to replay deployment or silently resume signing.
        # Make the explicitly reviewed fee settleable through the normal billing review instead.
        link = (await db.execute(select(ReferralLink).where(ReferralLink.referred_user_id == job.user_id))).scalar_one_or_none()
        platform, referral = split_fee(int(terms["amount_raw"]), bool(link and link.referrer_user_id != job.user_id))
        db.add(PlatformCharge(reference_key=reference, job_id=job.id, launch_id=launch.id,
            wallet_id=wallet.id, payer_user_id=job.user_id, kind="trade", network=wallet.network,
            asset=terms["asset"], token=None, decimals=terms["decimals"], amount_raw=int(terms["amount_raw"]),
            platform_raw=platform, referral_raw=referral, referrer_user_id=link.referrer_user_id if referral else None,
            referral_status="owed" if referral else "none", status="failed", destination=terms["destination"], terms=terms))
        result = SubmitResult(None, "failed", "Initial developer-buy fee needs settlement in Fees & payments")
    else:
        journal = (job.params or {}).get("execution") or {}
        job.params = {**job.params, "execution": {**journal, "dispatch_complete": False}}
        # Use the exact frozen terms: treasury changes cannot redirect an approved initial-buy payment.
        result = await collect(db, ex, job, wallet, "trade", terms, reference)
    previous = (job.params.get("execution") or {}).get("outcomes") or []
    outcomes = [SubmitResult(row.get("signature"), row["status"]) for row in previous]
    _finish(job, [*outcomes, result])
    job.params = {**job.params, "initial_buy_fee_handled": True}
    if result.status != "confirmed":
        job.params = {**job.params, "fee_outstanding": True,
                      "error": result.error or "Initial developer-buy fee requires reconciliation or settlement"}
    await db.commit()
    return result


async def reconcile(db, ex, charge):
    """Read existing transaction evidence only. Never signs or repeats a payment."""
    if charge.status not in {"pending", "processing"} or not charge.signature:
        return
    status = None
    if charge.network == "solana":
        status, _ = await ex.rpc.confirm(charge.signature, timeout_s=0)
    elif charge.network == "tron":
        from .extra_chains import tron_client
        receipt = await tron_client(charge.network).solid_info(charge.signature)
        if receipt:
            status = "failed" if receipt.get("result") == "FAILED" or (receipt.get("receipt") or {}).get("result") not in (None, "SUCCESS") else "confirmed"
    elif charge.network == "near":
        from .extra_chains import near_client
        from ..chains.near import outcome_status
        wallet = await db.get(Wallet, charge.wallet_id)
        receipt = await near_client(charge.network).status(charge.signature, wallet.address)
        status, _ = outcome_status(receipt)
    else:
        receipt = await ex.evm(charge.network).rpc.receipt(charge.signature)
        if receipt:
            raw = receipt.get("status")
            if raw in (1, "0x1"):
                status = "confirmed"
            elif raw in (0, "0x0"):
                status = "failed"
    if status not in {"confirmed", "failed"}:
        return
    charge.status, charge.updated_at = status, utcnow()
    event = (await db.execute(select(RevenueEvent).where(RevenueEvent.reference == f"charge:{charge.id}"))).scalar_one_or_none()
    if event:
        event.status = "received" if status == "confirmed" else "failed"
    from ..models import Job, Launch, Transaction
    for tx in (await db.execute(select(Transaction).where(Transaction.signature == charge.signature,
                                                          Transaction.job_id == charge.job_id))).scalars():
        tx.status = status
    if charge.kind == "launch":
        launch, job = await db.get(Launch, charge.launch_id), await db.get(Job, charge.job_id)
        sent = (await db.execute(select(Transaction.id).where(Transaction.job_id == charge.job_id,
                        Transaction.kind.like("deploy%"), Transaction.signature.is_not(None)))).first()
        if launch and job and not sent and job.status == "unconfirmed":
            # Only the fee was sent. The draft can now be retried using the credited payment.
            launch.status = "failed"
            job.status = "partial" if status == "confirmed" else "failed"
            job.params = {**job.params, "error": "Launch payment reconciled. Deployment was not sent; review and retry this draft."}
    await db.commit()
