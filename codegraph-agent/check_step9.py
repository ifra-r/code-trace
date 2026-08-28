"""Step 9 smoke test. Run FROM PROJECT ROOT: python check_step9.py

Calls the MCP tool functions in mcp_server/server.py directly as plain
Python (they're registered via mcp.add_tool(), not @mcp.tool(), specifically
so they stay callable like this without spinning up a real MCP client).

No LLM anywhere in this file -- that's the actual thing being verified: the
MCP layer is a pure GraphStore projection, zero narration, zero agent-layer
dependency. Real-repo checks reuse .codegraph/debug_requests.db from
check_step4.py (skipped, not failed, if missing).

Exit code 0 = green.
"""

import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []
SKIPPED = []
PROJECT_ROOT = Path(__file__).resolve().parent
DEBUG_DB_DIR = PROJECT_ROOT / ".codegraph"


class _Skip(Exception):
    pass


def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except _Skip as exc:
        SKIPPED.append(label)
        print(f"  SKIP  {label} ({exc})")
    except Exception:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
        traceback.print_exc()


def build(repo_url_or_path, db_path, *, is_local=False):
    from adapters.parsing.python_parser import PythonParser
    from adapters.storage.sqlite_store import SqliteGraphStore
    from adapters.vcs.github_scanner import GitHubScanner
    from core.graph.graph_builder import GraphBuilder

    store = SqliteGraphStore(str(db_path))
    parser = PythonParser()
    source = _LocalSource() if is_local else GitHubScanner()
    repo_id = GraphBuilder(source, parser, store).index_repo(repo_url_or_path)
    return store, repo_id


def open_debug_db(name: str):
    from adapters.storage.sqlite_store import SqliteGraphStore

    path = DEBUG_DB_DIR / name
    if not path.exists():
        raise _Skip(f"{path} not found -- run check_step4.py first")
    return SqliteGraphStore(str(path))


class _LocalSource:
    def clone(self, url: str) -> str:
        return url


def _one(store, repo_id, name, file_path="chain.py"):
    rows = store._conn.execute(
        "SELECT id FROM nodes WHERE repo_id=? AND name=? AND file_path=?",
        (repo_id, name, file_path),
    ).fetchall()
    assert len(rows) == 1, f"expected exactly one {name!r} in {file_path}, got {rows}"
    return rows[0][0]


# ---------------- synthetic fixture (same shape as check_step8.py's chain) ----------------

CHAIN_SRC = '''\
"""synthetic module for step 9 MCP tools"""


def step_a():
    return step_b()


def step_b():
    return step_c()


def step_c():
    return 99
'''


def _synth_store():
    proj_ctx = tempfile.TemporaryDirectory()
    db_ctx = tempfile.TemporaryDirectory()
    proj, dbdir = Path(proj_ctx.name), Path(db_ctx.name)
    (proj / "chain.py").write_text(CHAIN_SRC, encoding="utf-8")
    store, repo_id = build(str(proj), dbdir / "graph.db", is_local=True)
    store._keepalive = (proj_ctx, db_ctx)
    return store, repo_id
    


def _srv():
    import mcp_server.server as srv
    return srv


# ---------------- synthetic-project tool checks ----------------

def search_symbol_finds_step_a_without_source_text():
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)

    results = srv.search_symbol("step_a", limit=5)
    assert results, "expected at least one hit"
    assert results[0]["name"] == "step_a", f"expected step_a first, got {results[0]}"
    assert "source_text" not in results[0], \
        "search_symbol should return summaries only, no source_text (that's get_node/get_source)"


def get_node_returns_full_detail():
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)
    a_id = _one(store, repo_id, "step_a")

    node = srv.get_node(a_id)
    assert node["name"] == "step_a"
    assert "docstring" in node and "source_text" in node
    assert "return step_b()" in node["source_text"]


def get_node_unknown_id_returns_error_dict():
    srv = _srv()
    store, _repo_id = _synth_store()
    srv.set_store(store)
    result = srv.get_node(999_999)
    assert "error" in result


def get_callers_and_get_callees_are_consistent():
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)
    a_id, b_id = _one(store, repo_id, "step_a"), _one(store, repo_id, "step_b")

    callees_of_a = {n["id"] for n in srv.get_callees(a_id)}
    callers_of_b = {n["id"] for n in srv.get_callers(b_id)}
    assert b_id in callees_of_a, "step_a calls step_b"
    assert a_id in callers_of_b, "step_b is called by step_a"


def get_source_is_line_numbered():
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)
    a_id = _one(store, repo_id, "step_a")
    node = store.get_node(a_id)

    result = srv.get_source(a_id)
    first_line = result["source"].splitlines()[0]
    assert first_line.strip().startswith(str(node.start_line)), \
        f"expected the first line to start with line number {node.start_line}, got {first_line!r}"
    assert "def step_a" in first_line


def trace_path_has_no_narration_key_at_all():
    """The structural proof that this layer is zero-LLM: FlowTracer's dict
    always has a "steps" key (even if empty on error); this tool's dict
    must never have one at all."""
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)
    a_id = _one(store, repo_id, "step_a")

    result = srv.trace_path(a_id, end="step_c")
    names = [n["name"] for n in result["nodes"]]
    assert names == ["step_a", "step_b", "step_c"], f"unexpected path: {names}"
    assert len(result["edges"]) == 2
    assert "steps" not in result, "trace_path must never include narration -- that's FlowTracer's job"


def trace_path_unknown_start_returns_error():
    srv = _srv()
    store, _repo_id = _synth_store()
    srv.set_store(store)
    result = srv.trace_path(999_999)
    assert result["nodes"] == [] and "error" in result


def trace_path_unresolvable_end_returns_error():
    srv = _srv()
    store, repo_id = _synth_store()
    srv.set_store(store)
    a_id = _one(store, repo_id, "step_a")
    result = srv.trace_path(a_id, end="zzz_totally_unrelated_nonsense_zzz")
    assert result["nodes"] == [] and "error" in result


# ---------------- real repo (reuses debug_requests.db) ----------------

def requests_end_to_end_via_mcp_tools():
    srv = _srv()
    store = open_debug_db("debug_requests.db")
    srv.set_store(store)

    hits = srv.search_symbol("Session.request", limit=3)
    assert hits, "search_symbol found nothing for 'Session.request'"
    request = hits[0]
    assert request["name"] == "request" and request["file_path"].endswith("requests/sessions.py")

    callees = {n["name"] for n in srv.get_callees(request["id"])}
    assert "send" in callees, f"expected 'send' among Session.request's callees, got {callees}"

    result = srv.trace_path(request["id"], end="Session.send")
    names = [n["name"] for n in result["nodes"]]
    assert names == ["request", "send"], f"expected a 1-hop trace, got {names}"
    assert "steps" not in result, "MCP trace_path must be narration-free even on real data"
    print(f"        requests (via MCP tools): request -> send, "
          f"{len(result['edges'])} edge(s), resolved={result['edges'][0]['resolved']}")


def main() -> int:
    print("== CodeGraph Agent - step 9 smoke test ==")
    check("search_symbol: finds step_a, summary only (no source_text)",
          search_symbol_finds_step_a_without_source_text)
    check("get_node: full detail incl. docstring + source_text",
          get_node_returns_full_detail)
    check("get_node: unknown id -> {'error': ...}",
          get_node_unknown_id_returns_error_dict)
    check("get_callers/get_callees: consistent both directions",
          get_callers_and_get_callees_are_consistent)
    check("get_source: line-numbered source text",
          get_source_is_line_numbered)
    check("trace_path: correct path/edges, and NEVER a 'steps' key (zero-LLM proof)",
          trace_path_has_no_narration_key_at_all)
    check("trace_path: unknown start -> error",
          trace_path_unknown_start_returns_error)
    check("trace_path: unresolvable end -> error",
          trace_path_unresolvable_end_returns_error)
    check("REAL requests: search_symbol -> get_callees -> trace_path, all via MCP tools",
          requests_end_to_end_via_mcp_tools)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    if SKIPPED:
        print(f"\n{len(SKIPPED)} check(s) SKIPPED: {SKIPPED}")
    print("\nAll green. Step 9 done -- the bonus-reveal MCP layer exposes the "
          "same GraphStore the frontend will use, with zero new logic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())