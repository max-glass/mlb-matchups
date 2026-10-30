export const profitLanguage = {
  expected:
    "Expected net profit is the average result if the model's probability is right. It is not money you are promised.",
  potential: "Potential net profit is what the wager pays if it wins, after the stake.",
};

export function tierEmptyMessage(tier: string): string {
  return `No wager currently meets the ${tier} rules. A high win probability is not enough, and none of the verified prices clear this tier's value, uncertainty, and variance limits.`;
}

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}$${value.toFixed(2)}`;
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function american(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value > 0 ? `+${value}` : `${value}`;
}
