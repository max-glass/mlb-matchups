import { useEffect, useState } from "react";
import { api } from "../api";

export function HistoryPage() {
  const [card, setCard] = useState<{ id: string; version: number } | null>(null);
  const [snapshot, setSnapshot] = useState<string>("");
  const [performance, setPerformance] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ id: string; version: number }>("/api/cards/active").then(setCard).catch((err) => setError(err.message));
    api<Record<string, unknown>>("/api/performance").then(setPerformance).catch(() => undefined);
  }, []);

  async function load(version: number) {
    if (!card) return;
    const body = await api<unknown>(`/api/cards/${card.id}/versions/${version}`);
    setSnapshot(JSON.stringify(body, null, 2));
  }

  return (
    <div className="stack">
      <h1>Saved cards</h1>
      <p className="sub">A saved version keeps the odds and model probability from that moment. Refreshing the model does not rewrite it.</p>
      {error && <p>{error}</p>}
      {card && card.version === 0 && <p>No snapshot has been saved yet. Save the pregame card after you add selections.</p>}
      {card && card.version > 0 && (
        <div className="row">
          {Array.from({ length: card.version }, (_, index) => index + 1).map((version) => (
            <button key={version} className="ghost" onClick={() => load(version)}>Version {version}</button>
          ))}
        </div>
      )}
      {snapshot && <pre className="panel">{snapshot}</pre>}
      {performance && (
        <section className="panel">
          <h2>Settled results</h2>
          <p>Sample size: {String(performance.sample_size)}. {String(performance.warning)}</p>
          <p>Realized profit: {String(performance.realized_profit)}. Closing-line value: {String(performance.closing_line_value)}.</p>
        </section>
      )}
    </div>
  );
}
