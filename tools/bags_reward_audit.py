"""Read-only Bags deployer balance/history audit. No signing or fee collection."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))

from app.chains.bags import BagsAdapter
from app.chains.bags_rewards import audit_deployer_vault
from app.chains.solana import SolanaRPC
from app.chains.solana_plan import address
from app.config import get_settings


async def audit(wallet, maximum):
    address(wallet,'public deployer wallet')
    settings=get_settings()
    adapter=BagsAdapter(settings.bags_api_key,settings.bags_api_url)
    rpc=SolanaRPC(settings.solana_rpc_url)
    report={'wallet':wallet,'read_only':True,'network':'configured Solana RPC; not deployment authorization',
            'fee_collection_enabled':False}
    try:
        try:report['provider_positions']=(await adapter.deployer_positions(wallet)).public()
        except Exception as error:report['provider_positions']={'status':'unavailable','error_type':type(error).__name__}
        try:
            proof=await audit_deployer_vault(rpc,wallet,maximum_transactions=maximum)
            if proof is None:
                report['vault_audit']={'status':'no_vault_on_configured_network','fee_basis':None}
            else:
                report['vault_audit']={'status':'reconciled','finalized_slot':proof.slot,
                    'transactions':len(proof.signatures),'lifetime_raw':str(proof.state.lifetime),
                    'claimed_raw':str(proof.state.claimed),'pending_raw':str(proof.state.pending),
                    'attributed_payouts':[{'signature':p.signature,'event_ordinal':p.ordinal,
                        'mint':p.mint,'amount_raw':str(p.amount),'slot':p.slot} for p in proof.payouts]}
        except Exception as error:
            report['vault_audit']={'status':'unavailable_no_fee_basis','error_type':type(error).__name__}
    finally:
        await adapter._client.aclose();await rpc._client.aclose()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wallet',required=True,help='Public Solana deployer address only')
    parser.add_argument('--max-transactions',type=int,default=100)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    report=asyncio.run(audit(args.wallet,args.max_transactions))
    encoded=json.dumps(report,indent=2)
    if args.out:args.out.write_text(encoded,encoding='utf-8')
    print(encoded)


if __name__=='__main__':main()
