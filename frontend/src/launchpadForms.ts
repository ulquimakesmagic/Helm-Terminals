export interface LaunchField {
  key: string; type: string; label?: string; hint?: string;
  options?: (string | number)[]; default?: unknown; min?: number; max?: number;
}
interface Section { title: string; description: string; keys: string[] }
interface Profile { sections: Section[]; scope: string; pair: string; funding: string }
const execution: Section = { title: "Execution preferences", description: "These settings apply when you review and deploy the draft.", keys: ["priority_fee_sol", "slippage_pct"] };
const clanker: Profile = {
  sections: [{ title: "Pool protection", description: "Set the opening swap fee and how long it takes to decay.", keys: ["sniper_start_pct", "sniper_decay_s"] }, execution],
  scope: "Helm uses the WETH pool preset and the disclosed creator/Helm reward recipients. Custom pool currencies, vesting, airdrops and social launch tools are not supported here.", pair: "WETH", funding: "Creator buy",
};
const flap: Profile = {
  sections: [{ title: "Token type & trading tax", description: "Choose a standard token or a tax-token preset. Review the tax before deployment.", keys: ["tax_rate_pct"] }, execution],
  scope: "Helm's tax preset applies the same rate to buys and sells for one year, with creator tax sent to the dev wallet. Independent rates, custom allocations, vaults and payment tokens are not supported here.", pair: "Native asset", funding: "Creator purchase",
};
export const LAUNCHPAD_FORMS: Record<string, Profile> = {
  pumpfun: { sections: [execution], scope: "SOL curve with creator rewards. Custom quote assets, holder-reward mode, banners and video uploads are not supported here.", pair: "SOL", funding: "Creator buy" },
  letsbonk: { sections: [execution], scope: "Helm supports the SOL launch route with token artwork and an optional creator buy. Other BONKfun launch modes are not configured here.", pair: "SOL", funding: "Creator buy" },
  bags: { sections: [execution], scope: "Choose a supported pair below. Helm configures the disclosed creator/Helm fee split; social shareholders, holder rewards and Bags apps are not configurable here.", pair: "Selected asset", funding: "Creator buy" },
  "meteora-dbc": { sections: [{ title: "Curve & launch protection", description: "Select the Studio API preset and its anti-sniping setting.", keys: ["dbc_quote", "anti_sniping"] }, execution], scope: "Helm uses the integrated Studio API presets. Custom curves, creator vesting and the full native Studio configuration are not available here. An initial creator buy is not supported.", pair: "Curve currency", funding: "Creator buy unavailable" },
  clanker, "clanker-robinhood": clanker,
  "flap-sh": flap, "flap-sh-robinhood": flap,
  "flap-sh-base": { ...flap, sections: [{ title: "Base trading tax", description: "Choose the rate applied to buys and sells. Helm supports tax-token launches on Base.", keys: ["tax_rate_pct"] }, execution], scope: "Base supports the tax-token preset only: equal buy/sell rates for one year, with creator tax sent to the dev wallet. Flap's commission is separate from Helm's creator-reward share. Custom allocations and vaults are not supported here.", pair: "ETH" },
  pons: { sections: [{ title: "Advanced · Creator tax", description: "Optional creator tax for the Pons V2 ETH route. The dev wallet is the recipient.", keys: ["creator_tax_pct"] }, execution], scope: "Pons V2 with ETH pairing. Custom recipients, holder fee sharing, other paired assets and snipe exemptions are not configured by Helm.", pair: "ETH", funding: "Developer buy" },
  "four-meme": { sections: [{ title: "Classification", description: "Choose the category displayed for this token.", keys: ["label"] }, execution], scope: "Helm uses the BNB launch route. Open Mode, alternative raised tokens and configurable tax tokens are not supported here.", pair: "BNB", funding: "Creator buy" },
  "uniswap-v2": { sections: [{ title: "Supply & liquidity ownership", description: "Decide how much supply enters the pool and who can withdraw liquidity.", keys: ["lp_share_pct", "lp_tokens"] }, execution], scope: "Helm creates a token and a V2 ETH pool. This is not the full Uniswap position builder. Keeping LP tokens leaves liquidity withdrawable; burning them is irreversible.", pair: "ETH", funding: "Starting liquidity" },
  tolly: { sections: [], scope: "Helm uses Tolly's fixed USDC pool preset with locked liquidity. The protocol limits each wallet to 3% of supply for the first five minutes.", pair: "USDC", funding: "Creator buy" },
};
export function launchpadForm(id: string): Profile {
  return LAUNCHPAD_FORMS[id] ?? { sections: [execution], scope: "This launchpad is not configured for deployment yet.", pair: "Unavailable", funding: "Launch funding" };
}
export function groupedFields(id: string, fields: LaunchField[]) {
  const groups = launchpadForm(id).sections.map(section => ({ ...section, fields: section.keys.flatMap(key => fields.filter(f => f.key === key)) }));
  const used = new Set(groups.flatMap(g => g.fields.map(f => f.key)));
  const rest = fields.filter(f => f.key !== "dev_buy_sol" && !used.has(f.key));
  if (rest.length) groups.push({ title: "Additional launch settings", description: "Options supported by this launchpad.", keys: [], fields: rest });
  return groups.filter(g => g.fields.length);
}
export function validateLaunchFields(fields: LaunchField[], values: Record<string, unknown>): string | null {
  for (const f of fields) {
    const v = values[f.key];
    const name = f.label ?? f.key.replace(/_/g, " ");
    if (f.type === "select" && !f.options?.includes(v as string | number)) return `Choose a valid ${name}.`;
    if (f.type === "number") {
      if (v === "" || v === undefined || v === null || !Number.isFinite(Number(v))) return `Enter a valid ${name}.`;
      if (f.min !== undefined && Number(v) < f.min) return `${name} must be at least ${f.min}.`;
      if (f.max !== undefined && Number(v) > f.max) return `${name} must be at most ${f.max}.`;
    }
  }
  return null;
}
