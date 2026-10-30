import { NavLink, Route, Routes } from "react-router-dom";
import { CardPage } from "./pages/CardPage";
import { HistoryPage } from "./pages/HistoryPage";
import { MatchupPage } from "./pages/MatchupPage";
import { ModelPage } from "./pages/ModelPage";
import { PredictionCenter } from "./pages/PredictionCenter";

const links = [
  ["/", "Prediction Center"],
  ["/card", "My Pregame Card"],
  ["/matchups", "Matchups"],
  ["/history", "Saved Cards"],
  ["/model", "Model"],
];

export function App() {
  return (
    <div className="app">
      <nav>
        <div className="mark">Pregame</div>
        <strong>Prediction Center</strong>
        {links.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<PredictionCenter />} />
          <Route path="/card" element={<CardPage />} />
          <Route path="/matchups" element={<MatchupPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/model" element={<ModelPage />} />
        </Routes>
      </main>
    </div>
  );
}
