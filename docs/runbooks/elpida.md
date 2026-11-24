# Elpida in Helm Terminals

Helm supports Elpida V2.3 on Base, BNB Chain, Robinhood Chain and Ethereum. The earlier Base and BNB deployments use their original routers for trading and are included in discovery. New pools can use the native coin or the deployment's USDC or USDG. Helm uses the pool's existing quote asset to trade an existing token.

## Configuration

- Set the chain RPCs using HELM_RPC_8453, HELM_RPC_56, HELM_RPC_4663 and HELM_RPC_1. Historical state access allows Helm to verify older launches when the scanner omits them.
- Set PINATA_JWT for launch artwork and metadata. The Pinata credential remains on the API server.
- The Helm EVM treasury receives the usual Helm trading fee, subject to existing exemptions. Helm charges zero for deployment. Elpida charges its own $10 launch fee and pays 30% of its trade fees to the launching wallet. Both fees and gas appear separately in review.
- Set HELM_WEB_URL in the Telegram service if it uses a different Helm frontend. Telegram commands link to the selected launch or order and Helm's authenticated web app confirms the transaction.

The four active factory, hook and router ABIs are in [ElpidaV23.json](../../backend/app/chains/contracts/ElpidaV23.json). The Ethereum and Robinhood deployments have exact-match Sourcify records. For Base and BNB, the active runtimes matched those source-verified runtimes after masking the compiler's recorded immutable locations. Legacy trade routing uses the verified on-chain router creation receipts and checks the router's hook and PoolManager. [Sourcify factory](https://sourcify.dev/server/v2/contract/1/0x29f0D2A8F2DD5B2dF872222f10bE35D6B27aFD71?fields=abi) · [Base deployment record](https://github.com/AbdulganiyuMalik01/ELPIDA/blob/main/deployments/base-mainnet.json) · [BNB deployment record](https://github.com/AbdulganiyuMalik01/ELPIDA/blob/main/deployments/bnb-mainnet.json)

The scanner enriches factory events with artwork and social links. Helm checks each scanner launch against a receipt and can recover the IPFS metadata from the factory. The market header reads the pool price and swap volume directly from Uniswap v4. The USD figure for a stablecoin pair uses its dollar peg. An RPC that cannot supply a complete 24-hour log range leaves volume unavailable.

## Fork and real-funds checks

Run the regular backend suite for calldata, metadata, revert messages, discovery identity, and Telegram handoff tests. To run the contract round trips, install Anvil, set the four RPC environment variables and run the following from the backend directory. The optional HELM_ANVIL_BIN points to the Anvil executable.

~~~sh
python -m pytest tests/test_elpida.py tests/test_elpida_fork.py -q
~~~

The fork test creates temporary wallets in local Anvil and exercises a native launch and a stablecoin launch with a first buy, second-wallet buy, sell-back, token balances, and the factory's Launched and MetadataPublished events. Stablecoin balances are initialized in the local fork for those temporary wallets.

Small real-funds tests remain open. Use dedicated disposable test wallets to verify a Turnkey launch and round trip per chain, production Pinata publication and artwork display, at least one multi-wallet trade, wallet swaps, disperse and consolidate, and the Telegram links into authenticated review.
