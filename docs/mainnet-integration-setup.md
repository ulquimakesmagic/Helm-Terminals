# Mainnet setup and deferred integrations

Updated 16 November 2026. See the [current dollar budget](mainnet-budget-2026-11-16.md).
The operator has deferred funded mainnet deployments.
The redesign and removal of trading automation do not require deploying a factory.
No contract deployment or financial transaction was broadcast during this work.

## Pons V2: Robinhood Chain mainnet

The prepared factory is for **mainnet, chain ID 4663**, not testnet. It requires ETH
on Robinhood Chain. Testnet ETH cannot pay its gas. The factory has no constructor
arguments, owner or upgrade controls; each launch subsequently creates its own
immutable creator/treasury splitter. New splitters allocate 250 basis points
(2.5%) to Helm. Historical V1 splitters remain at their original immutable terms.

The unsigned local packet is `.audit-runtime/pons-v2-unsigned-deployment.json`.
That ignored file is a convenience, not a prerequisite: anyone with this checkout
can regenerate it using the committed tool below. The earlier estimate was
803,157 gas, approximately 0.0000161 ETH at that moment. Gas prices, estimates and
wallet overhead change; refresh the quote and fund more than the quoted minimum.

### Prepare without signing

From the repository root in PowerShell, set `EVM_RPC_ROBINHOOD` privately to your
Robinhood Chain HTTPS RPC. Then run:

```powershell
Push-Location contracts
npm ci --ignore-scripts
npm run check
Pop-Location
python tools/pons_factory.py prepare --out .audit-runtime/pons-v2-unsigned-deployment.json
```

Optionally pass `--from-address YOUR_PUBLIC_OPERATOR_ADDRESS` to estimate from the
funded account. The tool refuses a different chain, makes only read-only RPC
calls, and never signs, sends, stores a key or prints the RPC URL. This production
tool intentionally does not accept a different chain ID for testnet use.
Contract tests and a localhost mainnet fork are the rehearsal paths.

### Deploy later from your wallet

Review `contracts/HelmPonsRewardsV2.sol` and the artifact
`backend/app/chains/contracts/HelmPonsRewardsV2Factory.json`. The reproducible build
uses Solidity 0.8.26, optimizer enabled with 200 runs, EVM `paris`, and the exact
source inputs and CRLF normalization in `contracts/build.mjs`. Use the committed
artifact; compiling in an unrelated project can change its metadata/runtime hash.

Once funded, deploy the packet's `transaction.data` as **contract creation**, with
no destination and value zero, on chain 4663. No creator or treasury address is
passed to this factory constructor. The operator wallet supplies its current
nonce and fees. A transaction to an existing address is not a factory deployment.

If you use Foundry with an existing encrypted keystore account, the equivalent
PowerShell command is below. **This command signs and broadcasts; run it only
when you intend to deploy.** It has not been executed by the assistant.

```powershell
$ponsPacket = Get-Content .audit-runtime/pons-v2-unsigned-deployment.json -Raw | ConvertFrom-Json
cast send --rpc-url $env:EVM_RPC_ROBINHOOD --chain 4663 --account helm-operator --create $ponsPacket.transaction.data
```

`helm-operator` means your existing Foundry encrypted account name. A hardware
wallet is another supported signing method. Keep keys and recovery phrases out
of the repository and chat. See the [official cast send reference](https://www.getfoundry.sh/reference/cast/send).

Record the successful transaction hash and the receipt's `contractAddress`.
Verify its deployed bytecode before changing Helm's configuration:

```powershell
python tools/pons_factory.py verify --address YOUR_DEPLOYED_FACTORY_ADDRESS --out .audit-runtime/pons-v2-verification.json
```

Expected runtime SHA-256 for this build:
`e217d248cf9b5aec32537d0c78bd4d0ab064063132ef3cfd2ae3e7edc981fef9`.
The tool compares every runtime byte, not just the address or explorer label.
Explorer verification should use the exact compiler input from the build script,
including `RewardTestHelpers.sol`; a standalone recompile changes metadata.

### Configure and check Helm

In Railway's **API service**, set `PONS_REWARDS_FACTORY_V2` to the verified public
address and `PONS_REWARDS_ENABLED=true`. Retain any historical
`PONS_REWARDS_FACTORY` value; never point it at V2 or rewrite saved launch terms.
The creator-reward treasury must already be configured for Robinhood Chain.
Redeploy the API, then inspect `/launchpads` for Pons readiness and use a launch
preview from an eligible Pons wallet. The adapter independently verifies the V2
factory, Pons factory and escrow runtimes before execution. Pons wallet eligibility
is a separate upstream requirement; funding a wallet does not grant it.

If verification fails or the RPC/upstream contracts have changed, leave Pons in
setup-required state. No secret rotation, database wipe or redeploy of historical
splitters is necessary. An incorrect new-factory setting can be removed to disable
new launches while preserving historical claims.

## What the other setup states require

Some gates need engineering or upstream access. Funding a wallet or adding an API
key does not complete them. Do not change a status label to bypass a gate.

| Integration | Operator configuration / action | Work needed before enabling |
|---|---|---|
| LetsBonk / LaunchLab | Solana RPC and metadata provider; creator treasury configured | Implement and verify protocol-compatible creator-reward collection and the 2.5% share, with confirmed receipt accounting and recovery tests |
| Meteora DBC / Studio | Jupiter and metadata credentials are already configured in the current production environment | Complete DBC creator-reward collection, asset-aware splitting and claim/reconciliation tests; keep existing pool presets accurately scoped |
| Printr | Optional provider credential where required; Jupiter key for supported swaps | Verify its provider execution boundary and implement creator-reward collection and 2.5% accounting before enabling creation |
| Flap Base and taxed BNB / Robinhood launches | Correct chain RPC and creator treasury | Implement reward capture for creator tax, without treating protocol commission or holder dividends as creator rewards. Base has no verified standard zero-tax route in Helm |
| Bags alternative quote launches | Bags/Jupiter/metadata credentials already exist; discover eligible quote mints and units | Implement the current Fee Share V2/DBC transaction-plan flow, quote availability, staged signing, fee splitting and recovery. The enabled SOL route does not imply all pairings work |
| Clanker V5 on Base / BNB Chain / Robinhood Chain | Existing upstream factories; owned creator wallet funded for gas and any initial buy | Native/stable atomic buys, registered quote launch controls and claims are implemented; custom quotes require zero creator buy and live factory admission. Funded end-to-end rehearsals remain pending. See [V5 scope](clanker-v5-integration.md) |
| Bankr | Obtain suitable user/partner access from Bankr | Resolve Bankr deployment custody versus Helm's Turnkey model, verify recipient allocations and recoverable execution. Credentials alone do not integrate it |
| Surge / Anoncoin / Stonk.fun | Obtain official production contracts/API access and any allowlisting | Verify transaction construction, signer ownership, quote support, fee collection and failure recovery. No unverified endpoint should be enabled |
| Elpida variants | Obtain official mainnet contracts/program IDs and execution documentation | Validate each chain independently, creator-reward semantics, signing and fee collection; leave planned until reviewed |

For enabled chains, keep RPC credentials in Railway server variables. TRON uses
`TRONGRID_API_KEY` where applicable; NEAR and TRON must be included in
`EXTRA_NETWORKS`. Both production balance probes passed during this review.
Failures should appear as unavailable with retry, never as a fabricated zero.
Arc USDC uses its own native asset units; it must not be configured as ETH.

## Acceptance for each newly enabled route

Before calling a route available, verify chain ID and pinned contracts/programs,
create and trade previews, exact asset units, signer/recipient ownership, the
2.5% trade and creator-reward rules where applicable, and the 30% direct referral
liability from collected Helm fees. Exercise rejected signatures, stale quotes,
partial transactions, retries and reconciliation without duplicate payments.
Free Helm launches still have network and upstream launchpad costs; disclose those
in the confirmation preview. Initial Uniswap liquidity is not a developer trade.

See [the capability review](launchpad-capability-review-2026-11-15.md) for the
official protocol references and exact implemented scope. This guide does not
claim that the engineering gates above are already integrated.
