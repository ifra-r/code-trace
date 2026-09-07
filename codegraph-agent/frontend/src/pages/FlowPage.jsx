import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import "reactflow/dist/style.css";
import Layout from "../components/Layout.jsx";
import LoadingDots from "../components/LoadingDots.jsx";
import TraceNode from "../components/TraceNode.jsx";
import CitationChip from "../components/CitationChip.jsx";
import { api } from "../api.js";

const nodeTypes = { trace: TraceNode };

export default function FlowPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const repoUrl = params.get("repo");
  const repoName = params.get("name") || repoUrl;

  const [start, setStart] = useState("");
  const [target, setTarget] = useState("");
  const [status, setStatus] = useState("idle"); // idle | loading | done | empty | error
  const [error, setError] = useState("");
  const [trace, setTrace] = useState(null); // {nodes, edges, steps}
  const [selectedId, setSelectedId] = useState(null);
  const stepRefs = useRef({});

  useEffect(() => {
    if (!repoUrl) navigate("/");
  }, [repoUrl, navigate]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!start.trim() || status === "loading") return;
    setStatus("loading");
    setError("");
    setTrace(null);
    setSelectedId(null);
    try {
      const data = await api.trace(repoUrl, start.trim(), target.trim());
      if (!data.nodes || data.nodes.length === 0) {
        setError(data.error || "No call path found.");
        setStatus("empty");
        return;
      }
      setTrace(data);
      setStatus("done");
    } catch (err) {
      setError(err.message);
      setStatus("error");
    }
  }

  const onNodeClick = useCallback((_event, node) => {
    setSelectedId(node.id);
    stepRefs.current[node.id]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  if (!repoUrl) return null; // redirecting to "/"

  const flowNodes = trace
    ? trace.nodes.map((n, i) => ({
        id: String(n.id),
        type: "trace",
        position: { x: 0, y: i * 150 },
        selected: selectedId === String(n.id),
        data: { ...n, order: i + 1, isStart: i === 0, isEnd: i === trace.nodes.length - 1 },
      }))
    : [];

  const flowEdges = trace
    ? trace.edges.map((e) => ({
        id: `e-${e.source}-${e.target}`,
        source: String(e.source),
        target: String(e.target),
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)" },
        style: { stroke: "var(--accent)", strokeWidth: 2 },
      }))
    : [];

  return (
    <Layout repoName={repoName} repoUrl={repoUrl} mode="flow">
      <div className="flow-page">
        <form className="flow-form" onSubmit={handleSubmit}>
          <input
            type="text"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            placeholder="Start symbol (e.g. Session.request)"
            disabled={status === "loading"}
          />
          <input
            type="text"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Target symbol (optional)"
            disabled={status === "loading"}
          />
          <button type="submit" disabled={status === "loading" || !start.trim()}>
            {status === "loading" ? "Tracing…" : "Trace"}
          </button>
        </form>

        {status === "loading" && (
          <div className="flow-status">
            <LoadingDots label="Tracing call path" />
          </div>
        )}

        {(status === "error" || status === "empty") && (
          <div className="flow-status error-banner">
            <strong>{status === "empty" ? "No path found." : "Trace failed."}</strong>
            <p style={{ margin: "8px 0 0" }}>{error}</p>
          </div>
        )}

        {status === "done" && trace && (
          <div className="flow-result">
            <div className="flow-graph">
              <ReactFlow
                nodes={flowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                onNodeClick={onNodeClick}
                fitView
                proOptions={{ hideAttribution: true }}
              >
                <Background gap={18} color="var(--border)" />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>

            <div className="flow-steps">
              {trace.steps.map((step, i) => {
                const node = trace.nodes.find((n) => n.id === step.node_id);
                const idStr = String(step.node_id);
                return (
                  <div
                    key={idStr}
                    ref={(el) => (stepRefs.current[idStr] = el)}
                    className={`flow-step${selectedId === idStr ? " flow-step-selected" : ""}`}
                    onClick={() => setSelectedId(idStr)}
                  >
                    <div className="flow-step-order">{i + 1}</div>
                    <div className="flow-step-body">
                      <p className="flow-step-text">{step.text}</p>
                      {node && <CitationChip node={node} />}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}