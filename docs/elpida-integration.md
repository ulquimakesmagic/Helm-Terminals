# Elpida integration boundary

The [public Elpida repository](https://github.com/ulquimakesmagic/ELPIDA) describes a development prototype, with
Solana, Ethereum, Base, BNB Chain and Robinhood Chain targets. It does not establish production deployment readiness.
Helm therefore lists five planned entries and refuses deployment through them.

Before enabling an adapter, pin the deployed addresses, verified runtime/program hash, ABI/IDL, network IDs,
upgrade authority and oracle configuration. Implement create, quote, buy, sell, graduation and claim handling
against those pinned versions, with independent fixtures and controlled chain acceptance tests.

Helm launches are free. Standard Helm trades carry a 2.5% fee; admin accounts and explicitly waived users have
no Helm trading fee. A new Helm launch must configure the agreed 2.5% share of creator rewards to Helm while
preserving the creator's remaining 97.5%. Network costs and Elpida's own protocol creation/trading fees are
separate and must be disclosed before signing. A verified native split or equivalent enforceable collection
path is required before enabling new launches; a manual out-of-band payment is not an integration.

Require the protocol's valid first-buy semantics in launch review, validate the complete signed transaction and
minimum outputs, and reconcile existing signatures after uncertainty. Do not infer mainnet addresses from
testnet previews or enable a placeholder signer. An SDK release, website preview or configured RPC does not
establish that a mainnet program is deployed or ready.

The mainnet verification record must identify each chain separately: genesis/chain ID, deployed factory or
program, exact runtime/program hash, ABI/IDL revision, authorities and upgrade controls, registered quote assets,
their decimals, creation-fee payment assets, creator/holder reward rights, and the first-buy and graduation
rules. Include unsigned simulation fixtures and uncertain-send recovery tests before a funded acceptance run.
