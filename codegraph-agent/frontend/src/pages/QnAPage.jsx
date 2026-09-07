import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import ChatMessage from "../components/ChatMessage.jsx";
import { api } from "../api.js";

export default function QnAPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const repoUrl = params.get("repo");
  const repoName = params.get("name") || repoUrl;

  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (!repoUrl) navigate("/");
  }, [repoUrl, navigate]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSubmit(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;

    const questionId = crypto.randomUUID();
    const answerId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: questionId, role: "question", text: q },
      { id: answerId, role: "answer", pending: true },
    ]);
    setQuestion("");
    setBusy(true);

    try {
      const data = await api.ask(repoUrl, q);
      const chunks = (data.chunks || []).map((chunk) => ({
        text: chunk.text,
        nodes: chunk.node_ids.map((id) => data.nodes[String(id)]).filter(Boolean),
      }));
      setMessages((prev) =>
        prev.map((m) => (m.id === answerId ? { ...m, pending: false, chunks } : m))
      );
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === answerId ? { ...m, pending: false, error: err.message } : m
        )
      );
    } finally {
      setBusy(false);
    }
  }

  if (!repoUrl) return null; // redirecting to "/"

  return (
    <Layout repoName={repoName} repoUrl={repoUrl} mode="qna">
      <div className="chat-page">
        <div className="chat-log">
          {messages.length === 0 && (
            <p className="chat-empty">
              Ask anything about <strong>{repoName}</strong> — answers are
              grounded in real source, with clickable citations.
            </p>
          )}
          {messages.map((m) => (
            <ChatMessage key={m.id} {...m} />
          ))}
          <div ref={bottomRef} />
        </div>

        <form className="chat-input-row" onSubmit={handleSubmit}>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={`Ask a question about ${repoName}…`}
            disabled={busy}
            autoFocus
          />
          <button type="submit" disabled={busy || !question.trim()}>
            {busy ? "Asking…" : "Ask"}
          </button>
        </form>
      </div>
    </Layout>
  );
}