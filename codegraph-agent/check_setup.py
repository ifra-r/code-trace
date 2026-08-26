"""
Step 1 smoke test. Run FROM PROJECT ROOT:  python check_setup.py
Verifies frozen models, ports, stubs, deps, and that the FastAPI app boots.
Exit code 0 = green.
"""

import sys
import traceback

FAILURES = []


def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
        traceback.print_exc()


def core_models():
    from core.graph.models import Edge, EdgeType, Node, NodeType, Repo, Subgraph, Symbol

    n = Node(id=42, repo_id=1, type=NodeType.FUNCTION, name="Session.send",
             file_path="requests/sessions.py", start_line=100, end_line=120,
             source_text="def send(): ...", docstring="Sends a request.")
    # the citation contract: id -> type/file/lines survives a SQLite string round-trip
    assert NodeType(n.type.value) is NodeType.FUNCTION

    e = Edge(id=None, repo_id=1, source_node_id=n.id, target_node_id=7,
             type=EdgeType.CALLS, resolved=False)  # unresolved kept, not dropped
    assert EdgeType(e.type.value) is EdgeType.CALLS and e.resolved is False

    s = Symbol(type=NodeType.METHOD, name="send", file_path="x.py",
               start_line=1, end_line=3)
    assert s.parent_name is None

    sub = Subgraph(nodes=(n,), edges=(e,))
    assert len(sub.nodes) == 1 and len(sub.edges) == 1

    r = Repo(id=None, name="requests", source_url="https://github.com/psf/requests",
             local_path=".codegraph/repos/requests")
    assert r.indexed_at is None

    try:
        n.name = "mutated"  # frozen dataclass => must raise
        raise AssertionError("Node must be immutable")
    except Exception as ex:
        assert type(ex).__name__ == "FrozenInstanceError", ex


def enum_values_frozen():
    from core.graph.models import EdgeType, NodeType
    assert [t.value for t in NodeType] == ["file", "function", "class", "method"]
    assert [t.value for t in EdgeType] == ["calls", "imports", "inherits", "contains"]


def ports_import():
    from core.retrieval.interfaces import GraphStore, LLMProvider, Parser, RepoSource
    for m in ("get_node", "get_neighbors", "get_subgraph", "search_symbols"):
        assert hasattr(GraphStore, m), f"missing frozen GraphStore method: {m}"
    assert hasattr(LLMProvider, "complete")
    assert hasattr(Parser, "parse")
    assert hasattr(RepoSource, "clone")


def stubs_import():
    from adapters.llm.anthropic_provider import AnthropicProvider
    from adapters.parsing.python_parser import PythonParser
    from adapters.storage.sqlite_store import SqliteGraphStore
    from adapters.vcs.github_scanner import GitHubScanner
    from core.agent.evidence import validate_chunks
    from core.agent.flow_tracer import FlowTracer
    from core.agent.qa_agent import QAAgent
    from core.graph.graph_builder import GraphBuilder
    from core.graph.traversal import find_call_path
    from core.retrieval.bm25_retriever import BM25Index
    from core.retrieval.interfaces import GraphStore, LLMProvider, Parser, RepoSource

    assert isinstance(PythonParser(), Parser)
    assert isinstance(GitHubScanner(), RepoSource)
    assert isinstance(AnthropicProvider(), LLMProvider)
    assert isinstance(SqliteGraphStore(".codegraph/graph.db"), GraphStore)
    assert callable(validate_chunks) and callable(find_call_path)
    assert callable(BM25Index().search) and callable(QAAgent(None, None).answer)
    assert callable(FlowTracer(None, None).trace)


def third_party():
    import anthropic
    import fastapi
    import mcp
    import networkx
    import pydantic
    import rank_bm25
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()  # no-op if no .env; proves the import path works
    for mod in (fastapi, uvicorn, networkx, rank_bm25, anthropic, mcp, pydantic):
        print(f"        {mod.__name__}={getattr(mod, '__version__', 'installed')}")


def api_boots():
    from api.app import app
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        # httpx missing — degrade to route existence check
        assert "/health" in [getattr(r, "path", "") for r in app.routes]
        return
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def main() -> int:
    print("== CodeGraph Agent - step 1 smoke test ==")
    check("core models: construct/immutability/enum round-trip", core_models)
    check("enum values match frozen schema strings", enum_values_frozen)
    check("ports importable, frozen GraphStore surface intact", ports_import)
    check("all stubs importable, adapters satisfy ports", stubs_import)
    check("third-party deps present", third_party)
    check("FastAPI app boots, GET /health -> 200", api_boots)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("\nAll green. Step 1 locked. Next: step 2 (GitHubScanner.clone + API/React skeleton).")
    return 0


if __name__ == "__main__":
    sys.exit(main())