# Clanker V5 interface provenance

The ABI declarations in `ClankerV5.json` come from the official
[Clanker SDK](https://github.com/clanker-devco/clanker-sdk), version 5.1.0, which declares the MIT license.
The artifact records the reviewed source revision, published package revision and SHA-512 package integrity.
The selected declarations were checked for equality with the integrity-verified published package.

Factory, hook, escrow and token-deployer addresses come from the SDK and
[official deployment registry](https://docs.clanker.world/references/deployed-contracts).
Observed network-specific runtime SHA-256 values are pinned separately. These checks establish identity against
the reviewed deployment, not an independent security audit of Clanker's contracts.

The quote discovery snapshot contains factual addresses, labels and decimals from the
[official quote registry](https://docs.clanker.world/references/supported-quote-tokens), updated upstream
13 November 2026. Selection by symbol alone is unsafe: different addresses can share a symbol.
Helm rechecks registration and decimals against the selected chain's factory before each launch.

The Python adapter builds calldata locally. It does not execute SDK JavaScript, use the hosted deployment API,
accept caller-provided route bytes, import wallet keys or send funds during acceptance probes.
