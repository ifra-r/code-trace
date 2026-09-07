import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import LoadingDots from "../components/LoadingDots.jsx";
import { api } from "../api.js";

export default function LandingPage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState("idle"); // idle | indexing | confirm | done | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function runIndex(force) {
    setPhase("indexing");
    setError("");
    try {
      const data = await api.index(url.trim(), force);
      setResult(data);
      setPhase(data.status === "already_indexed" && !force ? "confirm" : "done");
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!url.trim() || phase === "indexing") return;
    runIndex(false);
  }

  function goTo(mode) {
    const repo = result.repo;
    navigate(
      `/${mode}?repo=${encodeURIComponent(repo.source_url)}&name=${encodeURIComponent(
        repo.name
      )}`
    );
  }

  return (
    <Layout>
      <div className="landing">
        <h1 className="landing-title">Understand any codebase, fast.</h1>
        <p className="landing-subtitle">
          Paste a public GitHub repo URL. CodeTrace builds a grounded code
          graph you can ask questions against or trace call paths through —
          every answer cites exact source lines.
        </p>

        <form className="landing-form" onSubmit={handleSubmit}>
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
            disabled={phase === "indexing"}
          />
          <button type="submit" disabled={phase === "indexing" || !url.trim()}>
            {phase === "indexing" ? "Indexing…" : "Index repository"}
          </button>
        </form>

        {phase === "indexing" && (
          <div className="landing-status indexing-status">
            <LoadingDots label="Cloning and indexing" />
          </div>
        )}

        {phase === "error" && (
          <div className="landing-status error-banner">
            <strong>Couldn't index that repo.</strong>
            <p style={{ margin: "8px 0 0" }}>{error}</p>
          </div>
        )}

        {phase === "confirm" && result && (
          <div className="landing-status confirm-banner">
            <p>
              <strong>{result.repo.name}</strong> was already indexed on{" "}
              {new Date(result.repo.indexed_at).toLocaleString()} —{" "}
              <span className="stat-figure">{result.node_count}</span> symbols,{" "}
              <span className="stat-figure">{result.edge_count}</span> edges.
            </p>
            <p className="confirm-question">Re-index to pick up changes?</p>
            <div className="confirm-actions">
              <button type="button" className="secondary" onClick={() => runIndex(true)}>
                Re-index
              </button>
              <button type="button" onClick={() => goTo("qna")}>
                Q&amp;A →
              </button>
              <button type="button" onClick={() => goTo("flow")}>
                Flow Trace →
              </button>
            </div>
          </div>
        )}

        {phase === "done" && result && (
          <div className="landing-status success-banner">
            <p>
              <strong>{result.repo.name}</strong> indexed —{" "}
              <span className="stat-figure">{result.node_count}</span> symbols,{" "}
              <span className="stat-figure">{result.edge_count}</span> edges.
            </p>
            <div className="confirm-actions">
              <button type="button" onClick={() => goTo("qna")}>
                Q&amp;A →
              </button>
              <button type="button" onClick={() => goTo("flow")}>
                Flow Trace →
              </button>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}