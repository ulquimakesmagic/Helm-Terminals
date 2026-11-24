# Brand logo sources

Every file here is the project's own logo, taken from the project's official website, brand kit or
repository and stored locally. Nothing is loaded from a remote URL at runtime. Files are used unaltered, except
the two marked "resized", which were scaled down to 256 px for bundle size (same artwork).

`components/marks.tsx` finds files by name (`chains/<chain id>`, `launchpads/<brand>`), so an official file
added here later appears automatically. Launchpad aliases (for example `flap-sh-robinhood` uses `flap`) are listed
in `PAD_BRAND` in that file.

Collected 2026-09-27. SHA-256 is of the file as received from the source. Copies written to a Windows checkout by
Claude's file transfer may carry an added C2PA provenance block (an SVG `<metadata>` element, or a PNG/JPEG metadata
segment), so their bytes differ; the artwork itself is unchanged.

## Chains

| File | Source | Why it's official | SHA-256 |
|---|---|---|---|
| `chains/solana.svg` | github.com/solana-foundation/solana-com @ e8a11bd2 `apps/web/public/src/img/branding/solanaLogoMark.svg` | Source of solana.com; the file is the logomark on solana.com/branding | 3d340110… |
| `chains/base.png` | Supplied by the Helm owner (blue ring mark, 224×224 PNG; white outside the ring made transparent, nothing else changed) | Owner-supplied | 7d2c8e9b… |
| `chains/bsc.svg` | github.com/bnb-chain/bnb-chain.github.io @ 001ecbc1 `docs/assets/bnb.svg` | BNB Chain's official docs site (docs.bnbchain.org) | b8ff1ce0… |
| `chains/ethereum.svg` | github.com/ethereum/ethereum-org-website @ 027a25f9 `public/images/assets/svgs/eth-glyph-colored.svg` | Source of ethereum.org; the file is on ethereum.org/assets | 86ba3592… |
| `chains/robinhood.svg` | cdn.robinhood.com/assets/generated_assets/hoodchain_docsite/feather-dark.svg | The feather symbol used by docs.robinhood.com/chain. Its brand guidelines (docs.robinhood.com/chain/brand-guidelines) reserve the standalone feather for compact spaces and approve black on Robin Neon #ccff00, which is how it's shown | 82ef124f… |
| `chains/tron.jpg` | Supplied by the Helm owner (400×400 JPEG, unchanged) | Owner-supplied: white TRON mark on red | b75b7b57… |
| `chains/near.jpg` | Supplied by the Helm owner (400×400 JPEG, unchanged) | Owner-supplied: green NEAR "N" on dark green | b1b67dda… |
| `chains/arc.jpg` | Supplied by the Helm owner (400×400 JPEG, unchanged) | Owner-supplied: silver Arc arch on navy | 4f5bed3c… |
| `chains/ton.jpg` | Supplied by the Helm owner (400×400 JPEG, unchanged) | Owner-supplied: white TON diamond on blue | 80008bf5… |

## Launchpads

| File | Used for | Source | Why it's official | SHA-256 |
|---|---|---|---|---|
| `launchpads/pumpfun.svg` | pumpfun | pump.fun/pump-logomark.svg | The logomark in pump.fun's own header | 2c72b8e0… |
| `launchpads/meteora.svg` | meteora-dbc | github.com/MeteoraAg/brand-kit @ 31e6db13 `meteora-v2/symbol/symbol.svg` | Meteora's own brand-kit repo | 04636199… |
| `launchpads/four-meme.svg` | four-meme | four.meme/_next/static/media/logo-square.05d-1ttqbfnze.svg | The square logo in four.meme's own header | a959325d… |
| `launchpads/uniswap.svg` | uniswap-v2 | github.com/Uniswap/interface @ 9023421e `apps/web/src/assets/svg/uniswap_app_logo.svg` | Uniswap's official front end (app.uniswap.org) | a1cfab6f… |
| `launchpads/printr.png` (resized) | printr | github.com/PrintrFi/printr-mcp @ c17be88f `apps/docs/public/icon-512.png` | Linked from Printr's docs (printr.gitbook.io); the org's site is printr.money. This is their docs app icon | 17ee0940… |
| `launchpads/bankr.png` | bankr | github.com/BankrBot/tokenized-agents @ 00950c65 `bankr-logo.png` | BankrBot org is linked from docs.bankr.bot; org site is bankr.bot | e84adabe… |
| `launchpads/flap.png` (resized) | flap-sh, flap-sh-robinhood | github.com/flap-sh/flap-vault-component-template @ df6c461a `public/logo.png` | Linked from docs.flap.sh | beb0861f… |
| `launchpads/pons.jpg` | pons | github.com/ponsdotdev/ponsfamily @ 74de0196 `media/logo.png` (a JPEG despite its name) | "Official repository for the ponsfamily.com smart contracts"; shown as "Pons Family" in its README | 26e844ee… |
| `launchpads/surge.png` | surge | surge.xyz/branding/apple-touch-icon.png | Surge's own site (currently a "coming soon" page); its only published mark | 1825e0f2… |
| `launchpads/elpida.png` | Elpida on four EVM chains | elpida.fun/assets/brand/icon-512.png | Organization logo published in Elpida's site metadata | 43c6d64d… |

## Supplied by the project owner (2026-09-27)

These five were provided directly by Helm's owner (the projects' profile images, 400 px JPEG; LetsBonk 304 px) after
the official sites couldn't be read from the build environment. They're shown unaltered and fill their tile
(LetsBonk's round badge sits in a round tile).

| File | Used for | Mark | SHA-256 |
|---|---|---|---|
| `launchpads/anoncoin.jpg` | anoncoin | White "anon coin" lettering on black | 47ac4d2d… |
| `launchpads/bags.jpg` | bags | White bag on black | e68e70dd… |
| `launchpads/stonkfun.jpg` | stonkfun | Pale-blue "S" of two linked shapes on near-black | bd1c5736… |
| `launchpads/clanker.jpg` | clanker, clanker-robinhood | Three rising purple bars on white (as in Clanker's header) | ba0a7eb8… |
| `launchpads/letsbonk.jpg` | letsbonk | Orange dog with a bat on an orange circle | 1dd633b8… |
| `launchpads/sunpump.jpg` | sunpump | Owner-supplied (400×400 JPEG, unchanged): orange sun with sunglasses | 7a322e72… |
| `launchpads/meme-cooking.jpg` | meme-cooking | Owner-supplied (400×400 JPEG, unchanged): chef dog on a teal badge | 269bdd1e… |
| `launchpads/tolly.jpg` | tolly | Owner-supplied (400×400 JPEG, unchanged): lime castle bridge on black | db823fed… |
| `launchpads/gaspump.jpg` | gaspump (planned) | Owner-supplied (400×400 JPEG, unchanged): red fuel pump on blue | 361b4b04… |

Every supported launchpad now has a logo.

To add one, save the official file as `launchpads/<brand>.svg` (or `.png`/`.webp`/`.jpg`/`.jpeg`, 128 px or larger) and
add its row above. If the logo carries its own background shape, set `fit: "fill"` for it in `TREATMENT` in
`components/marks.tsx`.
