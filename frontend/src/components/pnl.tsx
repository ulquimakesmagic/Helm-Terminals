/*
 * The shareable P&L card: one canvas renderer shown inline as an image and used for the PNG download.
 * Every number on it is a measured ledger figure; the caption keeps the "not everything is valued"
 * honesty next to the headline instead of only in small print somewhere else.
 */
import { useEffect, useState } from "react";
import { chainOf } from "../chains";

export type PnlFacts = {
  title: string;
  symbolLine: string;
  value: string;
  tone: "up" | "down";
  caption: string;
  foot?: string;
};

export function drawPnl(f: PnlFacts): string {
  const c = document.createElement("canvas");
  c.width = 1200; c.height = 630;
  const g = c.getContext("2d")!;
  g.fillStyle = "#06090b"; g.fillRect(0, 0, 1200, 630);
  g.strokeStyle = "rgba(120,170,160,0.07)"; g.lineWidth = 1;
  for (let x = 0; x <= 1200; x += 40) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 630); g.stroke(); }
  for (let y = 0; y <= 630; y += 40) { g.beginPath(); g.moveTo(0, y); g.lineTo(1200, y); g.stroke(); }
  g.fillStyle = "#35d0a0"; g.fillRect(70, 70, 4, 110);
  g.fillStyle = "#e4ecee"; g.font = "600 56px 'IBM Plex Sans', sans-serif"; g.fillText(f.title, 96, 128);
  g.fillStyle = "#6f8087"; g.font = "500 32px 'IBM Plex Mono', monospace"; g.fillText(f.symbolLine, 96, 176);
  g.fillStyle = f.tone === "up" ? "#3dcf8a" : "#ef6b6b"; g.font = "500 110px 'IBM Plex Mono', monospace";
  g.fillText(f.value, 70, 380);
  g.fillStyle = "#a7b5ba"; g.font = "28px 'IBM Plex Sans', sans-serif"; g.fillText(f.caption, 70, 440);
  if (f.foot) { g.fillStyle = "#6f8087"; g.font = "24px 'IBM Plex Sans', sans-serif"; g.fillText(f.foot, 70, 480); }
  g.fillStyle = "#35d0a0"; g.font = "600 28px 'IBM Plex Sans', sans-serif"; g.fillText("Helm Terminals", 70, 570);
  return c.toDataURL("image/png");
}

export function downloadPnl(facts: PnlFacts, filename: string) {
  const a = document.createElement("a");
  a.download = filename;
  a.href = drawPnl(facts);
  a.click();
}

/** The card as an image on the page, redrawn when the facts change (fonts awaited so it renders set). */
export function PnlImage({ facts, alt }: { facts: PnlFacts; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const key = JSON.stringify(facts);
  useEffect(() => {
    let live = true;
    (document.fonts?.ready ?? Promise.resolve()).then(() => { if (live) setSrc(drawPnl(JSON.parse(key))); });
    return () => { live = false; };
  }, [key]);
  if (!src) return null;
  return <img src={src} alt={alt} style={{ width: "100%", borderRadius: "var(--radius)", border: "1px solid var(--line)", display: "block" }} />;
}

/** Convenience facts for a launch's native-coin flow (the launch workdesk card). */
export function launchPnlFacts(launch: { name: string; symbol: string; network: string }, pnl: {
  net_sol_flow: number; confirmed_txs: number;
}): PnlFacts {
  const unit = chainOf(launch.network).native;
  return {
    title: launch.name,
    symbolLine: `$${launch.symbol}`,
    value: `${pnl.net_sol_flow >= 0 ? "+" : ""}${pnl.net_sol_flow.toFixed(3)} ${unit}`,
    tone: pnl.net_sol_flow >= 0 ? "up" : "down",
    caption: `Net ${unit} flow on ${chainOf(launch.network).name} · ${pnl.confirmed_txs} confirmed transactions`,
    foot: "Unsold tokens are not valued. Fees included.",
  };
}
