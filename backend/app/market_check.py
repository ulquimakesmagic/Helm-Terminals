"""One read-only Codex lookup using this backend's environment; never prints credentials or token data.

    python -m app.market_check --network solana --token <address>
"""

import argparse
import asyncio
import json

from .services.market import CODEX_NETWORK, Market, MarketError


async def check(network: str, token: str) -> int:
    market = Market()
    try:
        result = await market.snapshot(network, token)
    except MarketError as error:
        print(json.dumps({"state": "error", **error.details()}))
        return 1
    finally:
        await market.client.aclose()
    print(json.dumps({"state": result["state"], "source": "Codex"}))
    return 0 if result["state"] in {"ok", "no_pool", "not_indexed"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=sorted(CODEX_NETWORK), required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    return asyncio.run(check(args.network, args.token))


if __name__ == "__main__":
    raise SystemExit(main())
