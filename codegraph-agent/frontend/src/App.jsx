import { useEffect, useState } from "react";

const API = "/api"; // proxied to http://127.0.0.1:8000 by Vite

export default function App() {
  const [url, setUrl] = useState("https://github.com/psf/requests");
  const [status, setStatus] = useState("");
  const [backendUp, setBackendUp] = useState(false);
  const [events, setEvents] = useState([]);

  const log = (entry) =>
    setEvents((prev) => [{ time: new Date().toLocaleTimeString(), ...entry }, ...prev]);

  useEffect(() => {
    fetch(`${API}/health`)
      .then((r) => r.json())
      .then(() => setBackendUp(true))
      .catch(() => setBackendUp(false));
  }, []);

  async function call(path, body) {
    setStatus(`${path} …`);
    try {
      const res = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
      log({ kind: "ok", path, payload: data });
      setStatus(`${path} ✓`);
    } catch (err) {
      log({ kind: "error", path, payload: String(err) });
      setStatus(`${path} ✗ ${err.message}`);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>CodeGraph Agent</h1>
        <span className={backendUp ? "dot up" : "dot down"}>
          backend {backendUp ? "connected" : "offline"}
        </span>
      </header>

      <div className="controls">
        <input value={url} onChange={(e) => setUrl(e.target.value)} />
        <button onClick={() => call("/clone", { url })}>Clone</button>
        <button onClick={() => call("/index", { url })}>Index</button>
      </div>

      <p className="status">{status}</p>

      <ul className="log">
        {events.map((e, i) => (
          <li key={i} className={e.kind}>
            [{e.time}] {e.path} →
            <pre>{JSON.stringify(e.payload, null, 2)}</pre>
          </li>
        ))}
      </ul>
    </div>
  );
}