# Brand logo sources

Every file here is the project's own logo, taken from the project's official website, brand kit or
repository and stored locally. Nothing is loaded from a remote URL at runtime. Files are used unaltered, except
the two marked "resized", which were scaled down to 256 px for bundle size (same artwork).

`components/marks.tsx` finds files by name (`chains/<chain id>`, `launchpads/<brand>`), so an official file
added here later appears automatically. Launchpad aliases (for example `flap-sh-robinhood` uses `flap`) are listed
in `PAD_BRAND` in that file.

Collected 2026-09-27. SHA-256 is of the stored file.

## Chains

| File | Source | Why it's official | SHA-256 |
|---|---|---|---|
| `chains/solana.svg` | github.com/solana-foundation/solana-com @ e8a11bd2 `apps/web/public/src/img/branding/solanaLogoMark.svg` | Source of solana.com; the file is the logomark on solana.com/branding | 3d340110… |
| `chains/base.svg` | github.com/base-org/brand-kit @ eba9e730 `logo/TheSquare/Digital/Base_square_blue.svg` | Base's official brand kit repo | b4f2b487… |
| `chains/bsc.svg` | github.com/bnb-chain/bnb-chain.github.io @ 001ecbc1 `docs/assets/bnb.svg` | BNB Chain's official docs site (docs.bnbchain.org) | b8ff1ce0… |
| `chains/ethereum.svg` | github.com/ethereum/ethereum-org-website @ 027a25f9 `public/images/assets/svgs/eth-glyph-colored.svg` | Source of ethereum.org; the file is on ethereum.org/assets | 86ba3592… |
| `chains/robinhood.svg` | cdn.robinhood.com/assets/generated_assets/hoodchain_docsite/feather-dark.svg | The feather symbol used by docs.robinhood.com/chain. Its brand guidelines (docs.robinhood.com/chain/brand-guidelines) reserve the standalone feather for compact spaces and approve black on Robin Neon #ccff00, which is how it's shown | 82ef124f… |

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

## Missing (name shown, no logo)

| Launchpad | What was checked |
|---|---|
| LetsBonk (`letsbonk`) | Official mark is bonk.fun/logos/bonk_fun.png (2000 px PNG); it couldn't be brought into this environment. No official GitHub or npm copy. |
| Clanker (`clanker`, `clanker-robinhood`) | Official square mark is clanker.world/logo_circle.png; it couldn't be brought in. The brand kit linked from clanker-devco/DOCS is on Google Drive/Figma. The header SVG is the wide wordmark, not a square mark. |
| Bags (`bags`) | bagsfm repos and the @bagsfm/bags-sdk npm package contain no logo; bags.fm couldn't be opened from here. |
| Stonk.fun (`stonkfun`) | The official mark is stonkfun.xyz/stonk-mark.svg (listed on stonkfun.xyz/developers), but the site couldn't be opened from here. |
| Anoncoin (`anoncoin`) | docs.anoncoin.it lists no brand assets; anoncoin.it couldn't be opened from here. github.com/Anoncoin is an unrelated 2013 project. |

To add one, save the official file as `launchpads/<brand>.svg` (or `.png`/`.webp`/`.jpg`, 128 px or larger) and
add its row above. If the logo carries its own background shape, set `fit: "fill"` for it in `TREATMENT` in
`components/marks.tsx`.
