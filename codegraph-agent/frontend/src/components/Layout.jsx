import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function Layout({ repoName, repoUrl, mode, children }) {
  const [backendUp, setBackendUp] = useState(null); // null = still checking

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then(() => !cancelled && setBackendUp(true))
      .catch(() => !cancelled && setBackendUp(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const statusClass =
    backendUp === null ? "checking" : backendUp ? "up" : "down";
  const statusLabel =
    backendUp === null
      ? "checking backend…"
      : backendUp
      ? "backend connected"
      : "backend offline";

  const qs = repoUrl
    ? `?repo=${encodeURIComponent(repoUrl)}&name=${encodeURIComponent(repoName || "")}`
    : "";

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">Code</span>Trace
        </Link>
        {repoName && <span className="repo-pill">{repoName}</span>}
        {repoUrl && (
          <nav className="mode-tabs">
            <Link to={`/qna${qs}`} className={`mode-tab${mode === "qna" ? " active" : ""}`}>
              Q&amp;A
            </Link>
            <Link to={`/flow${qs}`} className={`mode-tab${mode === "flow" ? " active" : ""}`}>
              Flow Trace
            </Link>
          </nav>
        )}
        <span className={`status-dot ${statusClass}`}>{statusLabel}</span>
      </header>
      <main className="content">{children}</main>
    </div>
  );
}