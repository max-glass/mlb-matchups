import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { american, money, pct, profitLanguage, tierEmptyMessage } from "../format";

type Team = { id: number; name: string; abbreviation: string };
type Eval = {
  status: string;
  tier: string | null;
  p_win: number | null;
  p_loss: number | null;
  implied_raw: number | null;
  implied_novig: number | null;
  ev_per_unit: number | null;
  edge_vs_raw: number | null;
  p_positive_ev: number | null;
  variance_label: string | null;
  stake_dollars: number | null;
  stake_units: number | null;
  expected_profit_dollars: number | null;
  potential_profit_dollars: number | null;
  reason: string;
  explanation: string[];
  stake_explanation: string;
  american_odds: number | null;
  data_support: number | null;
  presented_as_current_opportunity: boolean;
};
type Row = {
  quote_id: number;
  sportsbook: string;
  source: string;
  quoted_at: string;
  market_type: string;
  selection: string;
  line: number | null;
  player_name?: string;
  game_label: string;
  evaluation: Eval;
};
type Report = {
  tiers: Record<string, Row[]>;
  ineligible: Row[];
  not_current: Row[];
  summary: { markets_analyzed: number; qualified_current: number; empty_tiers: string[]; note: string };
  odds_access: { note: string };
  risk_profile: string;
};

const TIER_ORDER = ["conservative", "balanced", "aggressive", "long_shot"] as const;
const TIER_LABEL: Record<string, string> = {
  conservative: "Conservative",
  balanced: "Balanced",
  aggressive: "Aggressive",
  long_shot: "Long Shot",
};

export function PredictionCenter() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [date, setDate] = useState("2026-10-19");
  const [away, setAway] = useState(114);
  const [home, setHome] = useState(139);
  const [analysisId, setAnalysisId] = useState(sessionStorage.getItem("analysisId") || "");
  const [report, setReport] = useState<Report | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [bankroll, setBankroll] = useState("");
  const [profile, setProfile] = useState("balanced");
  const [roster, setRoster] = useState<{ id: number; name: string }[]>([]);
  const [quote, setQuote] = useState({
    market_type: "player_hits",
    player_id: "",
    selection: "over",
    line: "0.5",
    american: "",
    other_side_american: "",
    team_side: "away",
    quoted_at: new Date().toISOString().slice(0, 16),
  });

  useEffect(() => {
    api<{ teams: Team[] }>("/api/teams").then((body) => setTeams(body.teams.sort((a, b) => a.name.localeCompare(b.name)))).catch((error) => setMessage(error.message));
    api<Record<string, unknown>>("/api/settings").then((body) => {
      if (body.bankroll) setBankroll(String(body.bankroll));
      if (body.risk_profile) setProfile(String(body.risk_profile));
    }).catch(() => undefined);
  }, []);

  async function runModel() {
    setBusy(true);
    setMessage("Running the pregame model. The first pull of schedules, logs, and Savant leaderboards can take a minute.");
    try {
      const body = await api<{ analysis_id: string }>("/api/matchups/analyze", {
        method: "POST",
        body: JSON.stringify({ date, away_team_id: away, home_team_id: home, simulation_count: 1200, seed: 20261019 }),
      });
      sessionStorage.setItem("analysisId", body.analysis_id);
      setAnalysisId(body.analysis_id);
      setMessage("Model finished. Add a verified Hard Rock price to see whether any tier qualifies.");
      await loadReport(body.analysis_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Model run failed.");
    } finally {
      setBusy(false);
    }
  }

  async function loadReport(id = analysisId) {
    if (!id) return;
    const [body, analysis] = await Promise.all([
      api<Report>(`/api/recommendations?analysis_id=${id}`),
      api<{ away: { batters: { player_id: number; name: string }[]; pitchers: { player_id: number; name: string }[] }; home: { batters: { player_id: number; name: string }[]; pitchers: { player_id: number; name: string }[] } }>(`/api/matchups/${id}`),
    ]);
    setReport(body);
    const people = [...analysis.away.batters, ...analysis.home.batters, ...analysis.away.pitchers, ...analysis.home.pitchers];
    setRoster(people.filter((person) => person.player_id).map((person) => ({ id: person.player_id, name: person.name })));
  }

  async function saveSettings() {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        bankroll: bankroll === "" ? null : Number(bankroll),
        risk_profile: profile,
        use_kelly: true,
      }),
    });
    setMessage("Bankroll settings saved. Probabilities are unchanged; stake eligibility can change.");
    await loadReport();
  }

  async function addPrice() {
    if (!analysisId) {
      setMessage("Run the model before entering a price.");
      return;
    }
    const analysis = await api<{ game: { game_pk: number; date: string; away_team_id: number; home_team_id: number } }>(`/api/matchups/${analysisId}`);
    await api("/api/betting/odds", {
      method: "POST",
      body: JSON.stringify({
        sportsbook: "Hard Rock Bet",
        source: "manual",
        quoted_at: new Date(quote.quoted_at).toISOString(),
        game_date: analysis.game.date,
        game_pk: analysis.game.game_pk,
        away_team_id: analysis.game.away_team_id,
        home_team_id: analysis.game.home_team_id,
        market_type: quote.market_type,
        player_id: quote.player_id ? Number(quote.player_id) : null,
        team_side: quote.team_side,
        selection: quote.selection,
        line: quote.line === "" ? null : Number(quote.line),
        american: Number(quote.american),
        other_side_american: quote.other_side_american === "" ? null : Number(quote.other_side_american),
      }),
    });
    setMessage("Price stored as a manual quote. It is not a live Hard Rock feed.");
    await loadReport();
  }

  async function addOne(row: Row) {
    await api("/api/cards/active/selections", {
      method: "POST",
      body: JSON.stringify({ analysis_id: analysisId, quote_id: row.quote_id }),
    });
    setMessage(`${row.player_name || row.selection} added to My Pregame Card as its own wager.`);
  }

  async function addWholeTier(tier: string) {
    const body = await api<{ note: string }>("/api/cards/active/tier", {
      method: "POST",
      body: JSON.stringify({ analysis_id: analysisId, tier }),
    });
    setMessage(body.note);
  }

  return (
    <div className="stack">
      <div>
        <h1>Prediction Center</h1>
        <p className="sub">
          Recommendations are grouped by value and risk. A 90% win probability at a short price can be a bad bet.
          Hard Rock Bet has no public odds API, so a tier stays empty until you enter or import a real price.
        </p>
      </div>
      {message && <div className="warn">{message}</div>}
      <div className="panel row">
        <label>Date<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label>Away
          <select value={away} onChange={(event) => setAway(Number(event.target.value))}>
            {teams.map((team) => <option key={team.id} value={team.id}>{team.abbreviation} — {team.name}</option>)}
          </select>
        </label>
        <label>Home
          <select value={home} onChange={(event) => setHome(Number(event.target.value))}>
            {teams.map((team) => <option key={team.id} value={team.id}>{team.abbreviation} — {team.name}</option>)}
          </select>
        </label>
        <button className="primary" disabled={busy} onClick={runModel}>{busy ? "Running…" : "Run model"}</button>
        <Link to="/matchups">Open matchup detail</Link>
      </div>
      <div className="panel row">
        <label>Hypothetical bankroll
          <input value={bankroll} placeholder="Leave blank until you choose one" onChange={(event) => setBankroll(event.target.value)} />
        </label>
        <label>Risk profile
          <select value={profile} onChange={(event) => setProfile(event.target.value)}>
            <option value="conservative">Conservative</option>
            <option value="balanced">Balanced</option>
            <option value="aggressive">Aggressive</option>
          </select>
        </label>
        <button className="ghost" onClick={saveSettings}>Save bankroll settings</button>
        <span className="sub">This does not change the model's probability. It changes which tiers receive a stake.</span>
      </div>
      <div className="tiers">
        {TIER_ORDER.map((tier) => {
          const rows = report?.tiers[tier] || [];
          const qualified = rows.filter((row) => row.evaluation.presented_as_current_opportunity);
          return (
            <section className="tier card" key={tier}>
              <h2>{TIER_LABEL[tier]}</h2>
              <div className="count">{qualified.length}</div>
              {rows.length === 0 ? <p className="empty">{tierEmptyMessage(TIER_LABEL[tier])}</p> : null}
              {qualified.length > 0 && (
                <button className="ghost" onClick={() => addWholeTier(tier)}>Add tier as separate wagers</button>
              )}
              {rows.map((row) => (
                <article className="rec" key={row.quote_id}>
                  <strong>{row.player_name || row.selection} {row.selection} {row.line ?? ""}</strong>
                  <div>{row.market_type} · {row.sportsbook} · {row.source}</div>
                  <div className="nums">
                    <span>Model</span><b>{pct(row.evaluation.p_win)}</b>
                    <span>Implied</span><b>{pct(row.evaluation.implied_raw)}</b>
                    <span>No-vig</span><b>{pct(row.evaluation.implied_novig)}</b>
                    <span>Expected ROI</span><b className={((row.evaluation.ev_per_unit || 0) > 0) ? "ok" : "bad"}>{pct(row.evaluation.ev_per_unit)}</b>
                    <span>Expected profit</span><b>{money(row.evaluation.expected_profit_dollars)}</b>
                    <span>Potential profit</span><b>{money(row.evaluation.potential_profit_dollars)}</b>
                    <span>Stake</span><b>{money(row.evaluation.stake_dollars)}</b>
                    <span>Status</span><b>{row.evaluation.status}</b>
                  </div>
                  <p>{row.evaluation.reason}</p>
                  <p className="sub">{profitLanguage.expected} {profitLanguage.potential}</p>
                  <button className="primary" onClick={() => addOne(row)}>Add to card</button>
                </article>
              ))}
            </section>
          );
        })}
      </div>
      <section className="panel stack">
        <h2>Enter a Hard Rock price</h2>
        <p className="sub">{report?.odds_access.note || "Manual and CSV prices are labeled. They are not treated as a live feed."}</p>
        <div className="row">
          <label>Market
            <select value={quote.market_type} onChange={(event) => setQuote({ ...quote, market_type: event.target.value })}>
              {["player_hits", "player_total_bases", "player_home_runs", "player_walks", "player_strikeouts", "player_runs", "player_rbis", "pitcher_strikeouts", "moneyline", "run_line", "total", "team_total", "f5_moneyline", "f5_total"].map((market) => <option key={market}>{market}</option>)}
            </select>
          </label>
          <label>Player
            <select value={quote.player_id} onChange={(event) => setQuote({ ...quote, player_id: event.target.value })}>
              <option value="">Team market</option>
              {roster.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
            </select>
          </label>
          <label>Side / team
            <select value={quote.selection} onChange={(event) => setQuote({ ...quote, selection: event.target.value })}>
              {["over", "under", "away", "home"].map((side) => <option key={side}>{side}</option>)}
            </select>
          </label>
          <label>Team side
            <select value={quote.team_side} onChange={(event) => setQuote({ ...quote, team_side: event.target.value })}>
              <option value="away">away</option>
              <option value="home">home</option>
            </select>
          </label>
          <label>Line<input value={quote.line} onChange={(event) => setQuote({ ...quote, line: event.target.value })} /></label>
          <label>American odds<input value={quote.american} onChange={(event) => setQuote({ ...quote, american: event.target.value })} /></label>
          <label>Other side<input placeholder="optional" value={quote.other_side_american} onChange={(event) => setQuote({ ...quote, other_side_american: event.target.value })} /></label>
          <label>Quoted at<input type="datetime-local" value={quote.quoted_at} onChange={(event) => setQuote({ ...quote, quoted_at: event.target.value })} /></label>
          <button className="primary" onClick={addPrice}>Store price</button>
        </div>
      </section>
      {report && report.not_current.length > 0 && (
        <section className="panel">
          <h2>Recorded prices that are not current offers</h2>
          <p className="sub">These quotes are stale, historical, or blocked by an exposure cap. They are not qualified bets you can place now. A historical pregame price can still be added to a card for review.</p>
          <table>
            <thead><tr><th>Market</th><th>Model</th><th>Expected ROI</th><th>Status</th><th>Why</th><th></th></tr></thead>
            <tbody>
              {report.not_current.map((row) => (
                <tr key={row.quote_id}>
                  <td>{row.player_name || row.game_label} {row.selection} {row.line ?? ""} · {row.source}</td>
                  <td>{pct(row.evaluation.p_win)}</td>
                  <td>{pct(row.evaluation.ev_per_unit)}</td>
                  <td>{row.evaluation.status}</td>
                  <td>{row.evaluation.reason}</td>
                  <td><button className="ghost" onClick={() => addOne(row)}>Add snapshot</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {report && report.ineligible.length > 0 && (
        <section className="panel">
          <h2>Not recommended</h2>
          <table>
            <thead><tr><th>Market</th><th>Price</th><th>Model</th><th>Expected ROI</th><th>Status</th><th>Why</th></tr></thead>
            <tbody>
              {report.ineligible.map((row) => (
                <tr key={row.quote_id}>
                  <td>{row.player_name || row.game_label} {row.market_type} {row.selection} {row.line ?? ""}</td>
                  <td>{american(row.evaluation.american_odds)} · {row.source}</td>
                  <td>{pct(row.evaluation.p_win)}</td>
                  <td className={((row.evaluation.ev_per_unit || 0) > 0) ? "ok" : "bad"}>{pct(row.evaluation.ev_per_unit)}</td>
                  <td>{row.evaluation.status}</td>
                  <td>{row.evaluation.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {report && <p className="sub">{report.summary.note} Profile: {report.risk_profile}. Qualified now: {report.summary.qualified_current}.</p>}
    </div>
  );
}
