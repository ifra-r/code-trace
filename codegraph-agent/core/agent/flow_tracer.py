"""Flow tracer: entry point lookup -> bounded call-path DFS -> per-step LLM
narration tied to nodes. Implemented in step 8.

Unlike qa_agent.py's citations, narration here needs no evidence.py-style
validation: the path is already fully known (deterministic graph traversal)
before the LLM is ever asked to describe it, one sentence per already-known
node. There's no LLM-supplied node_id to check against the store -- the
node_id is fixed by construction, only the sentence text comes from the LLM.
Narration failures (rate limits, etc.) fall back to a plain deterministic
sentence rather than failing the whole trace, matching the design doc's
"optional ... narration" framing (§9): the graph path/edges are the
load-bearing result, narration is a nice-to-have on top.
"""

from typing import Dict, List, Optional

from core.agent.qa_agent import _extract_symbol_candidates  # reused: same
# "does this query look like a symbol name" heuristic applies to flow-trace
# entry/target resolution as it does to Q&A's search_symbols calls.
from core.graph.models import EdgeType, Node
from core.graph.traversal import find_call_path
from core.retrieval.interfaces import GraphStore, LLMProvider

_MAX_NARRATION_SOURCE_CHARS = 800


def _resolve_symbol(store: GraphStore, text: str) -> Optional[Node]:
    """Best-effort: try identifier-like tokens extracted from `text` first
    (triggers bm25_retriever's exact-name boost), then fall back to a raw
    full-text search. Returns the single best match, or None."""
    for token in _extract_symbol_candidates(text):
        hits = store.search_symbols(token, limit=1)
        if hits:
            return hits[0]
    hits = store.search_symbols(text, limit=1)
    return hits[0] if hits else None


class FlowTracer:
    def __init__(self, store: GraphStore, llm: LLMProvider) -> None:
        self.store = store
        self.llm = llm

    def trace(self, query: str, target: Optional[str] = None) -> dict:
        """Returns ordered path (nodes + edges) for react-flow, with narration.

        Shape:
            {"nodes": [...], "edges": [...], "steps": [...]}                on success
            {"nodes": [], "edges": [], "steps": [], "error": "..."}         on failure
        """
        start = _resolve_symbol(self.store, query)
        if start is None:
            return self._empty(f"no symbol found matching {query!r}")

        goal: Optional[Node] = None
        if target is not None:
            goal = _resolve_symbol(self.store, target)
            if goal is None:
                return self._empty(f"no symbol found matching target {target!r}")

        path_nodes = find_call_path(self.store, start.id, goal.id if goal else None)
        if not path_nodes:
            where = f"from {start.name} to {goal.name}" if goal else f"from {start.name}"
            return self._empty(f"no call path found {where}")

        return {
            "nodes": [self._node_payload(n) for n in path_nodes],
            "edges": self._edges_for_path(path_nodes),
            "steps": self._narrate(path_nodes),
        }

    # ---------------- edges ----------------

    def _edges_for_path(self, path_nodes: List[Node]) -> List[dict]:
        """Only the CALLS edges directly between consecutive path steps, in
        path order -- a simple chain for react-flow to draw. get_subgraph's
        induced edges could include incidental extra CALLS edges among
        non-consecutive path nodes; those aren't part of the traced path
        itself, so they're intentionally excluded here rather than surfaced
        as unexplained extra arrows."""
        ids = [n.id for n in path_nodes]
        if len(ids) < 2:
            return []
        subgraph = self.store.get_subgraph(ids)
        by_pair = {
            (e.source_node_id, e.target_node_id): e
            for e in subgraph.edges if e.type == EdgeType.CALLS
        }
        edges = []
        for i in range(len(ids) - 1):
            edge = by_pair.get((ids[i], ids[i + 1]))
            if edge is not None:
                edges.append({
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "type": edge.type.value,
                    "resolved": edge.resolved,
                })
        return edges

    # ---------------- narration ----------------

    def _narrate(self, path_nodes: List[Node]) -> List[dict]:
        steps = []
        for i, node in enumerate(path_nodes):
            prev_node = path_nodes[i - 1] if i > 0 else None
            steps.append({"node_id": node.id, "text": self._narrate_step(node, prev_node)})
        return steps

    def _narrate_step(self, node: Node, prev_node: Optional[Node]) -> str:
        source = node.source_text[:_MAX_NARRATION_SOURCE_CHARS]
        if len(node.source_text) > _MAX_NARRATION_SOURCE_CHARS:
            source += "\n... (truncated)"
        context = (
            "This is the entry point of the trace."
            if prev_node is None
            else f"This step is reached because {prev_node.name} calls it."
        )
        prompt = f"""In exactly one plain sentence, explain what this function or
method does, for a call-flow trace being read step by step. {context} Do not
use markdown, do not mention the node id, output only the sentence.

{node.type.value} {node.name} ({node.file_path}:{node.start_line}-{node.end_line})
docstring: {node.docstring or '(none)'}
source:
{source}
"""
        try:
            text = self.llm.complete(prompt).strip()
        except Exception:
            # narration is a nice-to-have on top of the deterministic path
            # (§9) -- a rate limit or transient API error shouldn't fail the
            # whole trace, just fall back to a plain factual line.
            text = ""
        if not text:
            return f"{node.name} ({node.file_path}:{node.start_line})"
        return text.splitlines()[0].strip()

    # ---------------- payload helpers ----------------

    @staticmethod
    def _node_payload(node: Node) -> dict:
        return {
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
        }

    @staticmethod
    def _empty(error: str) -> dict:
        return {"nodes": [], "edges": [], "steps": [], "error": error}