import { useEffect, useState } from "react";
import { api } from "../api";

type Batter = {
  name: string;
  order: number;
  position?: string;
  bats?: string;
  expected_pa: number;
  hit_probability: number;
  expected_hits: number;
  multi_hit_probability: number;
  expected_total_bases: number;
  home_run_probability: number;
  expected_walks: number;
  expected_strikeouts: number;
  expected_runs: number;
  expected_rbis: number;
  data_support_label: string;
  notes: string[];
};
type Analysis = {
  analysis_id: string;
  experimental_reason: string;
  warnings: string[];
  game: { away_name: string; home_name: string; date: string; venue_name: string; status: string; start_time_utc?: string; schedule_note: string };
  win: { away_win_percent_display: number; home_win_percent_display: number; expected_away_runs: number; expected_home_runs: number };
  takeaways: { outlook: string[]; hitting: string[]; pitching: string[]; uncertainty: string[] };
  away: { batters: Batter[]; pitchers: { name: string; role: string; expected_strikeouts: number; expected_innings: number; availability_label: string }[] };
  home: { batters: Batter[]; pitchers: { name: string; role: string; expected_strikeouts: number; expected_innings: number; availability_label: string }[] };
  environment: { weather_note: string; park_factor_note: string; hr_environment_multiplier: number };
  series?: { description?: string; away_wins_before?: number; home_wins_before?: number; note?: string };
};

function mix(low: number, high: number, t: number) {
  const x = Math.max(0, Math.min(1, t));
  const a = x < 0.5 ? [176, 58, 46] : [214, 176, 64];
  const b = x < 0.5 ? [214, 176, 64] : [46, 125, 82];
  const u = x < 0.5 ? x / 0.5 : (x - 0.5) / 0.5;
  const c = a.map((channel, index) => Math.round(channel + (b[index] - channel) * u));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export function MatchupPage() {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Batter | null>(null);
  const id = sessionStorage.getItem("analysisId");

  useEffect(() => {
    if (!id) return;
    api<Analysis>(`/api/matchups/${id}`).then(setAnalysis).catch((err) => setError(err.message));
  }, [id]);

  if (!id) return <p>Run a model from the Prediction Center to see this matchup.</p>;
  if (error) return <p>{error}</p>;
  if (!analysis) return <p>Loading matchup…</p>;

  function table(title: string, rows: Batter[]) {
    const probs = rows.map((row) => row.hit_probability);
    const min = Math.min(...probs);
    const max = Math.max(...probs);
    return (
      <section className="panel">
        <h2>{title}</h2>
        <p className="sub">Colors compare hit probability inside this lineup only. Read the percentage before comparing teams. Green is higher within the team, red is lower. That is not a betting recommendation.</p>
        <table>
          <thead>
            <tr><th>Batter</th><th>Order</th><th>PA</th><th>Hit probability</th><th>Expected hits</th><th>Multi-hit</th><th>Total bases</th><th>HR</th><th>Walks</th><th>Strikeouts</th><th>Runs</th><th>RBI</th><th>Support</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const t = max === min ? 0.5 : (row.hit_probability - min) / (max - min);
              const label = t > 0.66 ? "Higher in lineup" : t < 0.33 ? "Lower in lineup" : "Middle of lineup";
              return (
                <tr key={row.name} onClick={() => setSelected(row)}>
                  <td>{row.name}</td>
                  <td>{row.order}</td>
                  <td>{row.expected_pa}</td>
                  <td><span className="hit" style={{ background: mix(min, max, t) }}>{(row.hit_probability * 100).toFixed(1)}% · {label}</span></td>
                  <td>{row.expected_hits.toFixed(2)}</td>
                  <td>{(row.multi_hit_probability * 100).toFixed(1)}%</td>
                  <td>{row.expected_total_bases.toFixed(2)}</td>
                  <td>{(row.home_run_probability * 100).toFixed(1)}%</td>
                  <td>{row.expected_walks.toFixed(2)}</td>
                  <td>{row.expected_strikeouts.toFixed(2)}</td>
                  <td>{row.expected_runs.toFixed(2)}</td>
                  <td>{row.expected_rbis.toFixed(2)}</td>
                  <td>{row.data_support_label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    );
  }

  return (
    <div className="stack">
      <div>
        <h1>{analysis.game.away_name} at {analysis.game.home_name}</h1>
        <p className="sub">{analysis.game.date} · {analysis.game.venue_name} · {analysis.game.status}. {analysis.game.schedule_note}</p>
      </div>
      <section className="panel">
        <strong>{analysis.win.away_win_percent_display.toFixed(1)}% / {analysis.win.home_win_percent_display.toFixed(1)}%</strong>
        <p>Expected runs {analysis.win.expected_away_runs.toFixed(2)}–{analysis.win.expected_home_runs.toFixed(2)}. These percentages are simulation frequencies.</p>
        {analysis.series && <p>{analysis.series.description}. Pregame series score {analysis.series.away_wins_before ?? "?"}–{analysis.series.home_wins_before ?? "?"}. {analysis.series.note}</p>}
        {analysis.takeaways.hitting.map((line) => <p key={line}>{line}</p>)}
        {analysis.takeaways.pitching.map((line) => <p key={line}>{line}</p>)}
        {analysis.warnings.map((line) => <p key={line} className="warn">{line}</p>)}
      </section>
      {table(analysis.game.away_name, analysis.away.batters)}
      {table(analysis.game.home_name, analysis.home.batters)}
      {selected && (
        <section className="panel">
          <h2>{selected.name}</h2>
          {selected.notes.map((note) => <p key={note}>{note}</p>)}
        </section>
      )}
      <section className="panel">
        <h2>Pitching</h2>
        {[...analysis.away.pitchers, ...analysis.home.pitchers].map((pitcher) => (
          <p key={pitcher.name}>{pitcher.name} · {pitcher.role} · K {pitcher.expected_strikeouts.toFixed(2)} · IP {pitcher.expected_innings.toFixed(2)} · {pitcher.availability_label}</p>
        ))}
      </section>
      <section className="panel">
        <h2>Environment</h2>
        <p>{analysis.environment.weather_note}</p>
        <p>{analysis.environment.park_factor_note}</p>
      </section>
    </div>
  );
}
