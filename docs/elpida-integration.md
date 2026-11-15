# Elpida integration boundary

The [public Elpida repository](https://github.com/ulquimakesmagic/ELPIDA) describes a development prototype, with
Solana, Ethereum, Base, BNB Chain and Robinhood Chain targets. It does not establish production deployment readiness.
Helm therefore lists five planned entries and refuses deployment through them.

Before enabling an adapter, pin the deployed addresses, verified runtime/program hash, ABI/IDL, network IDs,
upgrade authority and oracle configuration. Implement create, quote, buy, sell, graduation and claim handling
against those pinned versions, with independent fixtures and controlled chain acceptance tests.

Helm's $20 launch and 1% trade charges must be disclosed separately from Elpida's own protocol costs. Preserve
the creator's allocation; never insert a Helm creator-reward recipient. Require the protocol's valid first-buy
semantics in launch review, validate the complete signed transaction and minimum outputs, and reconcile existing
signatures after uncertainty. Do not infer mainnet addresses from testnet previews or enable a placeholder signer.
