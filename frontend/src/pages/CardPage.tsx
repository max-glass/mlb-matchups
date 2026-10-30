import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { american, money, pct, profitLanguage } from "../format";

type Selection = {
  id: string;
  stake_dollars: number;
  captured: {
    market_type: string;
    selection: string;
    line: number | null;
    player_name?: string;
    game_label?: string;
    source?: string;
    quoted_at?: string;
    evaluation: {
      p_win: number | null;
      implied_raw: number | null;
      ev_per_unit: number | null;
      american_odds: number | null;
      status: string;
      tier: string | null;
      expected_profit_dollars: number | null;
      potential_profit_dollars: number | null;
      explanation: string[];
      stake_explanation: string;
    };
  };
};
type Card = {
  id: string;
  version: number;
  document: { selections: Selection[]; parlays: unknown[]; name: string };
  exposure: {
    selection_count: number;
    parlay_count: number;
    staked_dollars: number;
    expected_profit_dollars: number;
    expected_roi: number | null;
    overlaps: { reason: string }[];
    roi_method: string;
  };
};

export function CardPage() {
  const [card, setCard] = useState<Card | null>(null);
  const [message, setMessage] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  async function load() {
    setCard(await api<Card>("/api/cards/active"));
  }
  useEffect(() => { load().catch((error) => setMessage(error.message)); }, []);

  async function save() {
    const body = await api<{ version: number }>("/api/cards/active/save", { method: "POST" });
    setMessage(`Saved version ${body.version}. Later price changes will not rewrite it.`);
    await load();
  }
  async function remove(id: string) {
    await api(`/api/cards/active/selections/${id}`, { method: "DELETE" });
    await load();
  }
  async function stake(id: string, value: string) {
    try {
      await api(`/api/cards/active/selections/${id}`, { method: "PATCH", body: JSON.stringify({ stake_dollars: Number(value) }) });
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Stake rejected.");
    }
  }

  return (
    <div className="stack">
      <div>
        <h1>My Pregame Card</h1>
        <p className="sub">Singles and parlays stay separate. The card stores the price and probability from the moment you added them.</p>
      </div>
      {message && <div className="warn">{message}</div>}
      {!card || card.document.selections.length === 0 ? (
        <section className="panel">
          <p>This card has no selections.</p>
          <Link to="/">Find opportunities in the Prediction Center</Link>
        </section>
      ) : (
        <>
          <section className="panel nums">
            <span>Selections</span><b>{card.exposure.selection_count}</b>
            <span>Parlays</span><b>{card.exposure.parlay_count}</b>
            <span>Total stake</span><b>{money(card.exposure.staked_dollars)}</b>
            <span>Expected net profit</span><b>{money(card.exposure.expected_profit_dollars)}</b>
            <span>Portfolio ROI</span><b>{pct(card.exposure.expected_roi)}</b>
          </section>
          <p className="sub">{card.exposure.roi_method} {profitLanguage.expected}</p>
          {card.exposure.overlaps.length > 0 && (
            <div className="warn">
              Correlated exposure: {card.exposure.overlaps.map((item) => item.reason).join(" ")}
              The expected profit still sums. The risk does not shrink just because the bets are listed separately.
            </div>
          )}
          <table>
            <thead>
              <tr><th>Wager</th><th>Odds</th><th>Model</th><th>Implied</th><th>Expected ROI</th><th>Stake</th><th>Expected profit</th><th>Potential profit</th><th></th></tr>
            </thead>
            <tbody>
              {card.document.selections.map((selection) => {
                const ev = selection.captured.evaluation;
                const expected = (selection.stake_dollars || 0) * (ev.ev_per_unit || 0);
                const potential = ev.american_odds && ev.american_odds > 0
                  ? selection.stake_dollars * (ev.american_odds / 100)
                  : ev.american_odds
                    ? selection.stake_dollars * (100 / Math.abs(ev.american_odds))
                    : null;
                return (
                  <tr key={selection.id}>
                    <td>
                      <button className="ghost" onClick={() => setOpen(open === selection.id ? null : selection.id)}>
                        {selection.captured.player_name || selection.captured.game_label} {selection.captured.market_type} {selection.captured.selection} {selection.captured.line ?? ""}
                      </button>
                      {open === selection.id && (
                        <div className="sub">
                          {(ev.explanation || []).map((line) => <p key={line}>{line}</p>)}
                          <p>{ev.stake_explanation}</p>
                          <p>Captured source: {selection.captured.source}. Quoted {selection.captured.quoted_at}. Tier {ev.tier || "none"}.</p>
                        </div>
                      )}
                    </td>
                    <td>{american(ev.american_odds)}</td>
                    <td>{pct(ev.p_win)}</td>
                    <td>{pct(ev.implied_raw)}</td>
                    <td>{pct(ev.ev_per_unit)}</td>
                    <td><input defaultValue={selection.stake_dollars} onBlur={(event) => stake(selection.id, event.target.value)} /></td>
                    <td>{money(expected)}</td>
                    <td>{money(potential)}</td>
                    <td><button className="ghost" onClick={() => remove(selection.id)}>Remove</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="sub">{profitLanguage.potential}</p>
          <button className="primary" onClick={save}>Save snapshot</button>
        </>
      )}
    </div>
  );
}
