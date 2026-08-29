import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function Layout({ repoName, children }) {
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

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">Code</span>Trace
        </Link>
        {repoName && <span className="repo-pill">{repoName}</span>}
        <span className={`status-dot ${statusClass}`}>{statusLabel}</span>
      </header>
      <main className="content">{children}</main>
    </div>
  );
}