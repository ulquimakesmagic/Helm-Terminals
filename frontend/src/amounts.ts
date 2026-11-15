/** Format base units exactly. Monetary values remain strings/BigInts until display. */
export function formatRaw(raw: string, decimals: number, maxPlaces = 6): string {
  if (!/^-?\d+$/.test(raw) || !Number.isInteger(decimals) || decimals < 0 || decimals > 77) return "Unavailable";
  const value = BigInt(raw), negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(decimals + 1, "0");
  const whole = decimals ? digits.slice(0, -decimals) : digits;
  const fraction = decimals ? digits.slice(-decimals).slice(0, maxPlaces).replace(/0+$/, "") : "";
  const tiny = value !== 0n && BigInt(whole) === 0n && !fraction;
  if (tiny) return `${negative ? "−" : ""}<${(10 ** -Math.min(decimals, maxPlaces)).toFixed(Math.min(decimals, maxPlaces))}`;
  return `${negative ? "−" : ""}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${fraction ? `.${fraction}` : ""}`;
}
