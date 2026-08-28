"""MCP server exposing GraphStore queries directly as tools (design doc §7).
Thin wrapper, NO new logic, no dependency on qa_agent/flow_tracer.

PACKAGE RENAME NOTE: originally /mcp per §6's directory layout, but that
name collides with the third-party `mcp` PyPI package (the official MCP
Python SDK) this file itself imports -- Python cannot resolve two different
things both named `mcp` from the same sys.path, so `from mcp.server.fastmcp
import FastMCP` inside a local package also named `mcp` breaks (either
self-imports, or the project's own adapters/core imports fail depending on
how it's invoked). Renamed to /mcp_server; same role, same "thin adapter"
contract as §6.

Run: CODEGRAPH_DB_PATH=.codegraph/graph.db python -m mcp_server.server
(defaults to .codegraph/graph.db if unset -- a provisional convention, since
api/app.py's /index endpoint doesn't wire a canonical per-repo db path yet
either; keep the two in sync once it does.)
"""

import os
from typing import List, Optional

# from mcp.server.fastmcp import FastMCP        # for version 1
from mcp.server.mcpserver import MCPServer      # for verison 2 of mcp (2.1.1)


from adapters.storage.sqlite_store import SqliteGraphStore
from core.graph.models import EdgeType, Node
from core.graph.traversal import find_call_edges, find_call_path
from core.retrieval.interfaces import GraphStore

_DEFAULT_DB_PATH = ".codegraph/graph.db"


def _default_store() -> GraphStore:
    return SqliteGraphStore(os.getenv("CODEGRAPH_DB_PATH", _DEFAULT_DB_PATH))


_store: GraphStore = _default_store()


def set_store(store: GraphStore) -> None:
    """Testing/embedding seam -- lets check_step9.py (or a future
    multi-repo launcher) point the server at a different store without
    re-importing this module or shelling out with a different env var."""
    global _store
    _store = store


mcp = MCPServer("codegraph-agent")


# ---------------- tool implementations ----------------
# Plain functions, registered with mcp.add_tool() below (not @mcp.tool())
# so they stay directly callable/testable as ordinary Python, independent
# of whatever wrapping the decorator form does.

def search_symbol(query: str, limit: int = 10) -> List[dict]:
    """Search for symbols by name/docstring/source text (BM25 + exact-name
    matching, per core/retrieval/bm25_retriever.py). Returns matching nodes
    with file/line -- no source text; use get_node/get_source for that."""
    return [_node_summary(n) for n in _store.search_symbols(query, limit=limit)]


def get_node(node_id: int) -> dict:
    """Full node detail + source snippet for a single node id."""
    node = _store.get_node(node_id)
    if node is None:
        return {"error": f"no node with id {node_id}"}
    return _node_full(node)


def get_callers(node_id: int) -> List[dict]:
    """Nodes that call this node (CALLS edges, direction='in')."""
    return [_node_summary(n)
            for n in _store.get_neighbors(node_id, EdgeType.CALLS, direction="in")]


def get_callees(node_id: int) -> List[dict]:
    """Nodes this node calls (CALLS edges, direction='out')."""
    return [_node_summary(n)
            for n in _store.get_neighbors(node_id, EdgeType.CALLS, direction="out")]


def get_source(node_id: int) -> dict:
    """Exact source snippet for a node, each line prefixed with its real
    line number ("line-highlighted" -- pure formatting, no LLM involved)."""
    node = _store.get_node(node_id)
    if node is None:
        return {"error": f"no node with id {node_id}"}
    numbered = "\n".join(
        f"{node.start_line + i:>5}  {line}"
        for i, line in enumerate(node.source_text.splitlines())
    )
    return {
        "node_id": node.id, "file_path": node.file_path,
        "start_line": node.start_line, "end_line": node.end_line,
        "source": numbered,
    }


def trace_path(start_node_id: int, end: Optional[str] = None) -> dict:
    """Ordered call path (nodes + edges) from start_node_id, optionally to
    `end` (a node id or a search query, resolved via search_symbols).
    Deterministic graph traversal only, zero LLM calls -- unlike
    FlowTracer.trace (core/agent/flow_tracer.py) there is no narration
    here. That's the point of this tool: proving an external MCP client
    gets the identical underlying graph, not our narration layer on top."""
    start = _store.get_node(start_node_id)
    if start is None:
        return {"nodes": [], "edges": [], "error": f"no node with id {start_node_id}"}

    goal_id = None
    if end is not None:
        goal = _resolve_end(end)
        if goal is None:
            return {"nodes": [], "edges": [], "error": f"no symbol found matching end={end!r}"}
        goal_id = goal.id

    path_nodes = find_call_path(_store, start_node_id, goal_id)
    if not path_nodes:
        return {"nodes": [], "edges": [], "error": "no call path found"}

    edges = find_call_edges(_store, path_nodes)
    return {
        "nodes": [_node_summary(n) for n in path_nodes],
        "edges": [
            {"source": e.source_node_id, "target": e.target_node_id,
             "type": e.type.value, "resolved": e.resolved}
            for e in edges
        ],
    }


# ---------------- internals ----------------

def _resolve_end(end: str) -> Optional[Node]:
    """Thin on purpose: try an integer id first, else a plain
    search_symbols lookup. Deliberately NOT reusing qa_agent's smarter
    identifier-extraction heuristic -- this file must not depend on agent
    code (see module docstring). If natural-language `end` queries prove
    unreliable in practice via MCP, promoting that heuristic into a shared
    core/retrieval module both layers can call is the right follow-up, not
    duplicating it here."""
    try:
        node_id = int(end)
    except (TypeError, ValueError):
        hits = _store.search_symbols(end, limit=1)
        return hits[0] if hits else None
    return _store.get_node(node_id)


def _node_summary(node: Node) -> dict:
    return {
        "id": node.id, "type": node.type.value, "name": node.name,
        "file_path": node.file_path, "start_line": node.start_line,
        "end_line": node.end_line,
    }


def _node_full(node: Node) -> dict:
    return {**_node_summary(node), "docstring": node.docstring,
            "source_text": node.source_text}


# ---------------- MCP registration ----------------

mcp.add_tool(search_symbol)
mcp.add_tool(get_node)
mcp.add_tool(get_callers)
mcp.add_tool(get_callees)
mcp.add_tool(get_source)
mcp.add_tool(trace_path)


if __name__ == "__main__":
    mcp.run()