# Landing imagery: Higgsfield prompts and placement

Generation needs the Higgsfield account and credits, so no images were generated. These prompts are ready to run;
the landing page works without them (its live, labelled example review card is the hero visual and remains the
fallback). Rules for every image: no logos of real chains or launchpads, no readable numbers, prices, charts with
values, user counts, testimonials or fake UI screenshots; abstract or illustrative only.

Palette for all prompts: near-black green-slate background `#06090b`–`#10171b`, single emerald accent `#35d0a0`
(with `#2aa39a`), restrained cool grey highlights `#a7b5ba`. Matte, precise, calm; no neon glow overload, no coins
raining, no rockets.

## 1. Coverage band — multichain infrastructure

- **Placement:** behind the "Coverage" section heading on `/`, full-bleed band, text on the left 45%.
- **Aspect / size:** 21:9, generate 2520×1080; serve 1680w and 840w WebP; mobile crops the right 60% at 4:3.
- **Prompt:** "Wide abstract architectural visualization of a calm network of nine distinct floating platforms
  connected by thin precise emerald light paths, viewed from a low three-quarter angle, dark matte green-slate
  environment #06090b, soft volumetric fog, platforms are minimal geometric slabs with subtle engraved grid lines,
  one accent colour emerald #35d0a0, cool grey rim light, generous empty space on the left third for text, no text,
  no logos, no numbers, cinematic but restrained, high detail, 8k."
- **Alt text:** "" (decorative; the section heading and chain list carry the meaning).

## 2. Wallet organization — "Wallets and supply" capability

- **Placement:** card illustration above the capability text, top of card.
- **Aspect:** 4:3, generate 1600×1200; serve 800w/400w.
- **Prompt:** "Isometric illustration of neatly arranged translucent glass folders and small sealed vault tiles on a
  dark green-slate desk surface #0c1114, thin emerald #35d0a0 outlines marking one selected group, orderly grid
  layout, soft studio lighting, precise and quiet mood, minimal detail, no text, no numbers, no logos, no coins."
- **Alt text:** "" (decorative).

## 3. Review before signing — Safeguards section

- **Placement:** right column of the Safeguards section on desktop; hidden below 768 px.
- **Aspect:** 3:4 portrait, generate 1200×1600; serve 600w.
- **Prompt:** "Abstract still life of a single blank card resting on a dark stone surface under a focused emerald
  light beam, a second identical faint card behind it slightly offset as if being compared, thin hairline frame
  details, dark green-slate background #06090b, calm, trustworthy, matte materials, no text, no numbers, no hands,
  no logos."
- **Alt text:** "" (decorative).

## 4. Launch coordination — hero accent (optional)

- **Placement:** subtle backdrop behind the hero example card at 20–30% opacity, desktop only.
- **Aspect:** 16:10, generate 1920×1200; serve 1280w.
- **Prompt:** "Top-down abstract view of a dark harbour control table, concentric emerald rings and short radial tick
  marks like a ship's helm compass etched into matte black glass, a few thin emerald guide lines converging on the
  centre, deep green-slate tones #06090b to #151e23, lots of negative space, minimalist, no text, no numerals, no
  logos."
- **Alt text:** "" (decorative).

## 5. Social card variant (optional)

- **Placement:** alternative to `public/og-image.png` (current card is typographic and needs no generation).
- **Aspect:** 1.91:1, 1200×630, keep the left 55% empty for the title overlay added in code.
- **Prompt:** "Minimal dark green-slate background #06090b with a softly lit emerald helm-wheel motif on the right
  side made of thin precise lines, subtle depth, no text, no logos, clean negative space on the left."

## When assets arrive

Convert to WebP (and AVIF if desired) at the listed widths, `loading="lazy"` and `decoding="async"` for everything
below the fold, explicit `width`/`height` to avoid layout shift, `alt=""` for decorative images, `fetchpriority` only
for an above-the-fold hero, and keep each file under ~150 KB. Respect `prefers-reduced-motion` if any are animated.
