"""Bags reward receipt accounting. This module never signs or transfers funds.

Native recipient payouts are received income. Proven deployer payouts create
fee debt only for explicitly opted-in new combined-policy launches. Referral
earnings on a debt are zero until the separate fee payment is confirmed.
"""
from hashlib import sha256

from sqlalchemy import select

from ..chains.bags_rewards import PROGRAM, WSOL, VaultAudit, account_keys, treasury_payout
from ..chains.solana_plan import address
from ..chains.base import AdapterError
from ..models import Job, Launch, PlatformCharge, ReferralLink, RevenueEvent, Transaction, Wallet, utcnow
from .charges import split_fee


async def record_native_payout(db, launch, job, signature, receipt):
    share = (launch.launchpad_params or {}).get('helm_creator_share') or {}
    treasury = share.get('recipient')
    if (launch.network != 'solana' or launch.launchpad != 'bags' or launch.imported
            or launch.user_id != job.user_id or launch.id != job.launch_id
            or not share.get('bps') or not treasury or not launch.mint_address):return False
    amount = treasury_payout(receipt, signature, launch.mint_address, treasury)
    if not amount:return False
    if not launch.dev_wallet_id:raise AdapterError('Bags payout is missing its owned developer wallet attribution')
    wallet = await db.get(Wallet, launch.dev_wallet_id)
    if not wallet or wallet.user_id != launch.user_id or wallet.network != 'solana':
        raise AdapterError('Bags payout is missing its owned developer wallet attribution')
    await db.execute(select(Launch.id).where(Launch.id == launch.id).with_for_update())
    key = 'bags:creator:'+sha256(f'{signature}:{launch.mint_address}:{treasury}'.encode()).hexdigest()
    exists = (await db.execute(select(PlatformCharge.id).where(PlatformCharge.reference_key == key))).first()
    if exists:return True
    link = (await db.execute(select(ReferralLink).where(ReferralLink.referred_user_id == launch.user_id))).scalar_one_or_none()
    platform, referral = split_fee(amount, bool(link and link.referrer_user_id != launch.user_id))
    evidence = {'method': 'finalized_bags_event_and_token_transfer', 'program': PROGRAM,
                'signature': signature, 'slot': receipt['slot'], 'mint': launch.mint_address,
                'quote_mint': WSOL, 'treasury': treasury, 'amount_raw': str(amount)}
    charge = PlatformCharge(reference_key=key, job_id=job.id, launch_id=launch.id, wallet_id=wallet.id,
        payer_user_id=launch.user_id, kind='creator_reward', network='solana', asset='WSOL', token=WSOL, decimals=9,
        amount_raw=amount, platform_raw=platform, referral_raw=referral,
        referrer_user_id=link.referrer_user_id if referral else None, referral_status='owed' if referral else 'none',
        status='confirmed', destination=treasury, signature=signature,
        terms={'stream': 'creator', 'collection': 'native_split', 'bps': share['bps'],
               'no_additional_claim_fee': True, 'receipt_evidence': evidence})
    db.add(charge)
    db.add(RevenueEvent(source='creator_share', network='solana', asset='WSOL', decimals=9,
        amount_raw=platform, estimated=False, status='received', destination=treasury,
        launch_id=launch.id, job_id=job.id, payer_user_id=launch.user_id,
        reference=f'charge:{charge.id}', receipt_evidence=evidence,
        note='Finalized Bags normal-recipient treasury payout; referral liability excluded. No extra claim fee.'))
    await db.flush()
    return True


async def reconcile(db, launch, rpc, job_id=None):
    """Original finalized claim receipts only; unavailable proof never becomes income."""
    if launch.launchpad != 'bags' or launch.network != 'solana':return
    query = select(Transaction, Job).join(Job, Job.id == Transaction.job_id).where(
        Job.launch_id == launch.id, Job.kind == 'claim_fees', Transaction.signature.is_not(None),
        Transaction.kind.in_(['claim_fees', 'claim_fees_setup']))
    if job_id:query = query.where(Job.id == job_id)
    touched = {}
    if job_id:
        job = await db.get(Job, job_id)
        if job and job.user_id == launch.user_id and job.launch_id == launch.id:touched[job.id] = job
    for tx, job in (await db.execute(query)).all():
        handled = dict((job.params or {}).get('bags_reward_receipts') or {})
        touched[job.id] = job
        if tx.signature in handled:continue
        share = (launch.launchpad_params or {}).get('helm_creator_share') or {}
        if launch.imported or not share.get('bps') or not share.get('recipient'):
            handled[tx.signature] = {'status': 'not_applicable_to_saved_terms'}
            job.params = {**job.params, 'bags_reward_receipts': handled}
            continue
        try:
            receipt = await rpc.get_transaction(tx.signature, commitment='finalized')
        except Exception:
            # Financial execution has already happened. Missing finality/proof
            # keeps accounting pending and never causes a second signature.
            continue
        if not receipt:continue
        try:
            if (receipt['transaction']['signatures'][0] != tx.signature or type(receipt['slot']) is not int
                    or receipt['slot'] < 0):
                raise AdapterError('Bags finalized receipt does not match the recorded transaction')
            if receipt['meta']['err'] is not None:
                state = 'failed_on_chain'
            elif PROGRAM not in account_keys(receipt):
                if any(line.startswith(f'Program {PROGRAM} ') for line in receipt['meta'].get('logMessages') or []):
                    raise AdapterError('Bags receipt is missing program account identity')
                state = 'no_bags_program_payment'
            else:
                paid = await record_native_payout(db, launch, job, tx.signature, receipt)
                state = 'recorded_treasury_payout' if paid else 'no_treasury_payout'
            handled[tx.signature] = {'status': state, 'slot': receipt['slot']}
            job.params = {**job.params, 'bags_reward_receipts': handled}
        except (AdapterError, KeyError, TypeError, ValueError, IndexError):
            continue
    for job in touched.values():
        expected = (await db.execute(select(Transaction.signature).where(Transaction.job_id == job.id,
            Transaction.kind.in_(['claim_fees', 'claim_fees_setup']), Transaction.signature.is_not(None)))).scalars().all()
        done = set((job.params or {}).get('bags_reward_receipts') or {})
        complete = bool(expected) and set(expected) <= done and job.status in ('confirmed', 'partial', 'failed')
        if not expected and job.status == 'failed':complete = True  # no signed lookup key was ever recorded
        job.params = {**job.params, 'bags_reward_accounting_complete': complete,
                      'bags_reward_checked_at': utcnow().isoformat()}


async def record_deployer_debts(db, wallet, job, audit: VaultAudit):
    """Explicit new-policy opt-in only. No referral income or Helm revenue is
    booked for an unpaid obligation, and no fee payment is dispatched here.
    """
    if wallet.user_id != job.user_id or wallet.kind != 'managed' or wallet.network != 'solana' or audit.state.wallet != wallet.address:
        raise AdapterError('Bags deployer accounting requires the owned managed wallet')
    launches = (await db.execute(select(Launch).where(Launch.user_id == wallet.user_id,
        Launch.network == 'solana', Launch.launchpad == 'bags', Launch.status.in_(('live','archived')),
        Launch.imported.is_(False)))).scalars().all()
    by_mint = {}
    for launch in launches:
        by_mint.setdefault(launch.mint_address, []).append(launch)
    recorded = 0
    for payout in audit.payouts:
        if payout.signature not in audit.signatures or payout.slot > audit.slot:
            raise AdapterError('Bags payout is outside the finalized audit checkpoint')
        candidates = by_mint.get(payout.mint, [])
        if len(candidates) != 1:continue  # Never allocate a shared withdrawal to an arbitrary launch.
        launch = candidates[0]
        terms = (launch.launchpad_params or {}).get('bags_reward_policy') or {}
        share = (launch.launchpad_params or {}).get('helm_creator_share') or {}
        if (type(terms.get('version')) is not int or terms.get('version') != 1 or terms.get('bps') != 250 or terms.get('streams') != ['creator', 'deployer']
                or terms.get('deployer') != wallet.address or terms.get('quote_mint') != WSOL
                or type(terms.get('creation_slot')) is not int or terms['creation_slot'] < 0 or payout.slot <= terms['creation_slot']
                or share.get('bps') != 250 or not share.get('recipient')):continue
        treasury = share['recipient']
        address(treasury, 'reward treasury')
        if treasury == wallet.address:raise AdapterError('Bags creator and reward treasury must be different wallets')
        amount = payout.amount * 250 // 10000
        if not amount:continue
        await db.execute(select(Launch.id).where(Launch.id == launch.id).with_for_update())
        key = 'bags:deployer:'+sha256(f'{payout.signature}:{payout.ordinal}:{payout.mint}:{wallet.address}'.encode()).hexdigest()
        if (await db.execute(select(PlatformCharge.id).where(PlatformCharge.reference_key == key))).first():continue
        db.add(PlatformCharge(reference_key=key, job_id=job.id, launch_id=launch.id, wallet_id=wallet.id,
            payer_user_id=wallet.user_id, kind='creator_reward', network='solana', asset='SOL', decimals=9,
            amount_raw=amount, platform_raw=amount, referral_raw=0, referral_status='none', status='owed',
            destination=treasury, terms={'stream': 'deployer', 'bps': 250, 'basis_raw': str(payout.amount),
                'collection': 'verified_deployer_debt', 'withdrawal_signature': payout.signature,
                'withdrawal_ordinal': payout.ordinal, 'finalized_audit_slot': audit.slot,
                'unattributed_income_excluded': True, 'no_automatic_payment': True}))
        await db.flush();recorded += 1
    return recorded
