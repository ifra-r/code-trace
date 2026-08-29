const API = "/api"; // proxied to http://127.0.0.1:8000 by Vite

async function request(path, { method = "GET", body } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `${res.status} ${res.statusText}`);
  }
  return data;
}

export const api = {
  health: () => request("/health"),
  index: (url, force = false) =>
    request("/index", { method: "POST", body: { url, force } }),
  ask: (url, question) =>
    request("/ask", { method: "POST", body: { url, question } }),
};