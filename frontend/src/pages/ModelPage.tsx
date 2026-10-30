import { useEffect, useState } from "react";
import { api } from "../api";

export function ModelPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { api<Record<string, unknown>>("/api/health").then(setHealth); }, []);
  return (
    <div className="stack">
      <h1>Model</h1>
      <section className="panel">
        <p>Version {String(health?.model_version || "…")}.</p>
        <p>{String(health?.odds_access || "")}</p>
        <p>Live betting is not part of this release. Pregame quotes and live quotes would be stored as different contexts.</p>
        <p>Tiers use positive expected value, a Monte Carlo sampling check, data support, and a variance band. Win probability is not one of the tier rules. The rule file is <code>tier_rules.json</code>.</p>
        <p>Uncertainty is a normal approximation around the simulation frequency plus a configured data-support gap. It is not a calibrated posterior, and the page says so on each explanation.</p>
        <p>Stake sizing is fractional Kelly, then a tier cap. Long Shot uses the smallest cap. Correlation with the current card multiplies the stake by 0.6. Full Kelly is not the default.</p>
      </section>
    </div>
  );
}
