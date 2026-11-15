export type ClaimMode = "in-app" | "external" | "automatic" | "none" | "auction";

export interface LaunchpadExperience {
  mode: string;
  title: string;
  description: string;
  features: string[];
  settingsTitle: string;
  rewardTitle: string;
  rewardCopy: string;
  claimMode: ClaimMode;
  claimCopy: string;
  claimUrl?: string;
  accent: string;
}

const DEFAULT: LaunchpadExperience = {
  mode: "Token launch",
  title: "Configure this launch",
  description: "Helm will show only the settings supported by this launchpad.",
  features: ["Launchpad-native deployment", "Review before signing"],
  settingsTitle: "Launch settings",
  rewardTitle: "Creator rewards",
  rewardCopy: "No creator-reward split is configured for this launchpad.",
  claimMode: "none",
  claimCopy: "This launchpad has no creator-fee claim supported by Helm.",
  accent: "#74f0c6",
};

export const LAUNCHPAD_EXPERIENCES: Record<string, LaunchpadExperience> = {
  "flap-sh-base": {
    mode: "Tax token", title: "Launch on Flap.sh for Base",
    description: "Launch Flap's supported tax-token preset on Base, funded in ETH.",
    features: ["Tax-token launch", "Creator tax to dev wallet", "Reward collection pending"],
    settingsTitle: "Flap Base token economics", rewardTitle: "Flap creator tax",
    rewardCopy: "Creator tax goes to the dev wallet. Taxed launches require a verified 2.5% reward-collection integration; standard zero-tax launches remain available.",
    claimMode: "automatic", claimCopy: "Creator tax is paid to the dev wallet by Flap's mechanism; no Helm claim is needed.", accent: "#4d7cff",
  },
  pumpfun: {
    mode: "Bonding curve", title: "Launch on Pump.fun",
    description: "Create on Pump.fun's bonding curve, with an optional creator buy and priority fee.",
    features: ["Pump.fun metadata", "97.5% creator rewards", "Creator fees distributable in Helm"],
    settingsTitle: "Pump.fun curve settings", rewardTitle: "Pump.fun creator fees",
    rewardCopy: "New Helm launches allocate 97.5% of creator rewards to you and 2.5% to Helm through Pump.fun sharing. Existing on-chain configurations retain their recipients.",
    claimMode: "in-app", claimCopy: "Distribute available Pump.fun fees directly to every configured shareholder.", accent: "#55e878",
  },
  letsbonk: {
    mode: "Bonding curve", title: "Launch on LetsBonk",
    description: "Create a LetsBonk token with its required artwork and optional creator buy.",
    features: ["Image required", "Optional creator buy", "Graduates through LetsBonk"],
    settingsTitle: "LetsBonk curve settings", rewardTitle: "LetsBonk rewards",
    rewardCopy: "LetsBonk requires a verified 2.5% creator-reward collection integration before new Helm launches can be enabled.",
    claimMode: "external", claimCopy: "LetsBonk creator fees are claimed on bonk.fun.", claimUrl: "https://bonk.fun/", accent: "#ff9d2e",
  },
  bags: {
    mode: "Bonding curve or paired pool", title: "Launch on Bags",
    description: "Choose a SOL curve or an eligible paired asset. Bags' fee-share configuration is written at launch.",
    features: ["97.5% creator rewards", "SOL or supported pair", "Permanent liquidity after graduation"],
    settingsTitle: "Bags launch settings", rewardTitle: "Bags royalty split",
    rewardCopy: "SOL launches configure a 97.5% creator / 2.5% Helm royalty split. Pair-token launches require a verified collection integration before deployment.",
    claimMode: "in-app", claimCopy: "SOL-paired fees can be claimed in Helm. Other pair-token fees are claimed on Bags.", accent: "#ff5aa5",
  },
  printr: {
    mode: "Bonding curve", title: "Launch on Printr",
    description: "Create a Printr token on Solana with a creator buy. Printr builds the launch; your dev wallet signs it.",
    features: ["SOL bonding curve", "Creator buy at launch", "Creator fees to your dev wallet"],
    settingsTitle: "Printr launch settings", rewardTitle: "Printr creator fees",
    rewardCopy: "Printr creator fees require a verified 2.5% collection integration before new Helm launches can be enabled.",
    claimMode: "external", claimCopy: "Claim creator fees on app.printr.money for now.", accent: "#ff6b3d",
  },
  "meteora-dbc": {
    mode: "Dynamic bonding curve", title: "Launch with Meteora DBC",
    description: "Use Jupiter Studio's Meteora curve, quote currency and anti-sniping configuration.",
    features: ["SOL or USDC curve", "Optional anti-sniping", "Locked pool after migration"],
    settingsTitle: "Dynamic curve settings", rewardTitle: "Meteora creator fees",
    rewardCopy: "The 2.5% creator-reward collection integration for this route is pending; new deployment is unavailable until verified.",
    claimMode: "in-app", claimCopy: "Claim creator fees through the supported Jupiter Studio flow.", accent: "#8258ff",
  },
  clanker: {
    mode: "Uniswap V4 pool", title: "Launch with Clanker",
    description: "Create a Base token with locked liquidity and Clanker's configurable descending sniper fee.",
    features: ["Locked V4 liquidity", "Descending sniper fee", "97.5% creator LP rewards"],
    settingsTitle: "Clanker pool settings", rewardTitle: "Clanker LP rewards",
    rewardCopy: "New Helm launches allocate 97.5% of creator-side locker rewards to you and 2.5% to Helm. Historical locker allocations retain their on-chain terms.",
    claimMode: "in-app", claimCopy: "Collect the creator wallet's WETH and token rewards from Clanker's locker.", accent: "#4d7cff",
  },
  "clanker-robinhood": {
    mode: "Uniswap V4 pool", title: "Launch with Clanker on Robinhood Chain",
    description: "Create with locked liquidity and Clanker's descending sniper-fee module.",
    features: ["Locked V4 liquidity", "Descending sniper fee", "97.5% creator LP rewards"],
    settingsTitle: "Clanker pool settings", rewardTitle: "Clanker LP rewards",
    rewardCopy: "New Helm launches allocate 97.5% of creator-side locker rewards to you and 2.5% to Helm. Historical locker allocations retain their on-chain terms.",
    claimMode: "in-app", claimCopy: "Collect the creator wallet's rewards from Clanker's locker.", accent: "#caff35",
  },
  "flap-sh": {
    mode: "Standard or tax token", title: "Launch on Flap.sh",
    description: "Choose a standard launch or Flap's supported one-year trading-tax token.",
    features: ["Standard or tax token", "Creator tax paid automatically", "Reward collection pending"],
    settingsTitle: "Flap token economics", rewardTitle: "Flap revenue",
    rewardCopy: "Creator tax goes directly to the dev wallet. Taxed launches require a verified 2.5% reward-collection integration; standard zero-tax launches remain available.",
    claimMode: "automatic", claimCopy: "Creator tax is paid automatically to the dev wallet and does not need a Helm claim.", accent: "#ffca3a",
  },
  "flap-sh-robinhood": {
    mode: "Standard or tax token", title: "Launch on Flap.sh for Robinhood Chain",
    description: "Choose a standard launch or Flap's supported one-year trading-tax token.",
    features: ["Standard or tax token", "Creator tax paid automatically", "Reward collection pending"],
    settingsTitle: "Flap token economics", rewardTitle: "Flap revenue",
    rewardCopy: "Creator tax goes directly to the dev wallet. Taxed launches require a verified 2.5% reward-collection integration; standard zero-tax launches remain available.",
    claimMode: "automatic", claimCopy: "Creator tax is paid automatically to the dev wallet.", accent: "#caff35",
  },
  pons: {
    mode: "Bonding curve", title: "Launch on Pons",
    description: "Configure the creator tax before the token starts on Pons' Robinhood Chain curve.",
    features: ["Optional creator tax", "Creator tax escrow", "Uniswap V4 graduation"],
    settingsTitle: "Pons curve settings", rewardTitle: "Pons creator rewards",
    rewardCopy: "New launches require the verified 97.5% creator / 2.5% Helm factory. Until that factory is deployed and configured, creation is unavailable. Historical immutable splitters retain their original terms.",
    claimMode: "in-app", claimCopy: "Legacy launches claim the selected wallet's combined Pons rewards with no Helm deduction.", accent: "#caff35",
  },
  "four-meme": {
    mode: "Bonding curve", title: "Launch on Four.meme",
    description: "Create a standard Four.meme token and choose the category shown on the launchpad.",
    features: ["Four.meme category", "Optional creator buy", "PancakeSwap graduation"],
    settingsTitle: "Four.meme curve settings", rewardTitle: "Creator rewards",
    rewardCopy: "Standard Four.meme launches do not expose a creator-reward stream to split. Token tax is a separate token-economic feature and is not treated as Helm revenue.",
    claimMode: "none", claimCopy: "Standard Four.meme tokens have no creator-fee claim in Helm.", accent: "#f3ba2f",
  },
  "uniswap-v2": {
    mode: "Direct liquidity pool", title: "Create a Uniswap V2 pool",
    description: "Deploy a fixed-supply token and seed its first ETH pair directly, with explicit LP-token handling.",
    features: ["Fixed 1B supply", "Choose pool allocation", "Burn or keep LP tokens"],
    settingsTitle: "Pool and liquidity settings", rewardTitle: "Pool fees",
    rewardCopy: "Uniswap fees belong to the LP-token holder. They are not creator rewards, and burning LP tokens permanently gives up control of the position.",
    claimMode: "none", claimCopy: "There is no separate creator-fee claim for this pool.", accent: "#ff4db8",
  },
  sunpump: {
    mode: "TRON bonding curve", title: "Launch on SunPump",
    description: "Create directly on SunPump's TRON bonding curve with an optional TRX creator buy.",
    features: ["TRON-native creation", "Optional creator buy", "SunSwap after graduation"],
    settingsTitle: "SunPump curve settings", rewardTitle: "Creator rewards",
    rewardCopy: "Helm's SunPump integration has no verified native creator-reward split.",
    claimMode: "none", claimCopy: "No creator-fee claim is available through Helm for SunPump.", accent: "#ff2638",
  },
  "meme-cooking": {
    mode: "Fair-launch auction", title: "Start a Meme.Cooking auction",
    description: "Set the auction duration and caps. Participants deposit at one shared final price.",
    features: ["Shared auction price", "Soft-cap refunds", "Optional hard-cap close"],
    settingsTitle: "Auction settings", rewardTitle: "Auction settlement",
    rewardCopy: "Auction deposits and refunds follow Meme.Cooking's settlement rules; Helm does not take a creator-reward share.",
    claimMode: "auction", claimCopy: "After settlement, claim tokens or a refund for each participating wallet.", accent: "#00ec97",
  },
  tolly: {
    mode: "Locked Uniswap V3 pool", title: "Launch with Tolly",
    description: "Seed a token/USDC pool on Arc with Tolly's fixed early max-wallet protection.",
    features: ["USDC starting buy", "Locked V3 liquidity", "3% max wallet for 5 minutes"],
    settingsTitle: "Tolly pool settings", rewardTitle: "Creator rewards",
    rewardCopy: "Helm has no verified Tolly creator-reward split and does not redirect pool funds.",
    claimMode: "none", claimCopy: "No creator-fee claim is available through Helm for Tolly.", accent: "#7b61ff",
  },
};

export function launchpadExperience(id: string): LaunchpadExperience {
  return LAUNCHPAD_EXPERIENCES[id] ?? DEFAULT;
}
