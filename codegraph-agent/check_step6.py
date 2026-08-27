"""Step 6 smoke test. Run FROM PROJECT ROOT: python check_step6.py

Synthetic-project checks build a fresh throwaway DB and pin exact values for
get_node / get_neighbors / get_subgraph. Real-repo checks reuse the debug DBs
written by check_step4.py's real-repo runs (.codegraph/debug_requests.db,
.codegraph/debug_flask.db) if present -- run check_step4.py first if you
want those included; they're skipped (not failed) otherwise.

Exit code 0 = green."""

import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []
SKIPPED = []
PROJECT_ROOT = Path(__file__).resolve().parent
DEBUG_DB_DIR = PROJECT_ROOT / ".codegraph"


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


class _Skip(Exception):
    pass


def build(repo_url_or_path, db_path, *, is_local=False):
    from core.graph.graph_builder import GraphBuilder
    from adapters.parsing.python_parser import PythonParser
    from adapters.storage.sqlite_store import SqliteGraphStore
    from adapters.vcs.github_scanner import GitHubScanner

    store = SqliteGraphStore(str(db_path))
    parser = PythonParser()
    source = _LocalSource() if is_local else GitHubScanner()
    builder = GraphBuilder(source, parser, store)
    repo_id = builder.index_repo(repo_url_or_path)
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


# ---------------- synthetic project (same fixture as check_step4.py) ----------------

GOOD_SRC = '''\
"""synthetic module for step 6"""
from .other import other_func


def helper(x):
    return x + 1


def caller(x):
    return helper(x) + undefined_dynamic_thing()


class Box:
    def __init__(self, val):
        self.val = val

    def get(self):
        return self.val

    def double(self):
        return self.get() + self.get()


def uses_cross_file():
    return other_func()
'''

OTHER_SRC = '''\
def other_func():
    return 42
'''


def make_synth_project(tmp: Path) -> None:
    (tmp / "good.py").write_text(GOOD_SRC, encoding="utf-8")
    (tmp / "other.py").write_text(OTHER_SRC, encoding="utf-8")


def _synth_store():
    proj_ctx = tempfile.TemporaryDirectory()
    db_ctx = tempfile.TemporaryDirectory()
    proj, dbdir = Path(proj_ctx.name), Path(db_ctx.name)
    make_synth_project(proj)
    # store, repo_id = build(proj, dbdir / "graph.db", is_local=True)
    store, repo_id = build(str(proj), dbdir / "graph.db", is_local=True)
    # keep the tempdirs alive for the caller's lifetime by stashing them
    store._keepalive = (proj_ctx, db_ctx)
    return store, repo_id


def _one(store, repo_id, name, file_path):
    rows = store._conn.execute(
        "SELECT id FROM nodes WHERE repo_id=? AND name=? AND file_path=?",
        (repo_id, name, file_path),
    ).fetchall()
    assert len(rows) == 1, f"expected exactly one {name!r} in {file_path}, got {rows}"
    return rows[0][0]


# ---------------- get_node ----------------

def get_node_returns_correct_fields():
    from core.graph.models import NodeType

    store, repo_id = _synth_store()
    helper_id = _one(store, repo_id, "helper", "good.py")

    node = store.get_node(helper_id)
    assert node is not None
    assert node.id == helper_id
    assert node.name == "helper"
    assert node.type == NodeType.FUNCTION
    assert node.file_path == "good.py"
    assert "return x + 1" in node.source_text


def get_node_unknown_id_returns_none():
    store, _repo_id = _synth_store()
    assert store.get_node(999_999) is None


# ---------------- get_neighbors ----------------

def get_neighbors_out_calls_deduplicated():
    store, repo_id = _synth_store()
    double_id = _one(store, repo_id, "double", "good.py")
    get_id = _one(store, repo_id, "get", "good.py")

    from core.graph.models import EdgeType
    neighbors = store.get_neighbors(double_id, EdgeType.CALLS, direction="out")
    # double() calls self.get() TWICE -- two edges, one neighbor node
    assert [n.id for n in neighbors] == [get_id], \
        f"expected exactly one deduplicated neighbor (get), got {[n.name for n in neighbors]}"


def get_neighbors_in_direction_is_callers():
    store, repo_id = _synth_store()
    double_id = _one(store, repo_id, "double", "good.py")
    get_id = _one(store, repo_id, "get", "good.py")

    from core.graph.models import EdgeType
    callers = store.get_neighbors(get_id, EdgeType.CALLS, direction="in")
    assert [c.id for c in callers] == [double_id], \
        f"expected double as the only caller of get, got {[c.name for c in callers]}"


def get_neighbors_edge_type_filter_excludes_other_types():
    store, repo_id = _synth_store()
    file_id = store._conn.execute(
        "SELECT id FROM nodes WHERE repo_id=? AND type='file' AND file_path='good.py'",
        (repo_id,),
    ).fetchone()[0]

    from core.graph.models import EdgeType
    contains = store.get_neighbors(file_id, EdgeType.CONTAINS, direction="out")
    calls = store.get_neighbors(file_id, EdgeType.CALLS, direction="out")
    assert len(contains) == 4, f"file should CONTAIN 4 top-level symbols, got {len(contains)}"
    assert calls == [], "a FILE node has no outgoing CALLS edges"


def get_neighbors_both_direction_unions_out_and_in():
    store, repo_id = _synth_store()
    box_id = _one(store, repo_id, "Box", "good.py")
    file_id = store._conn.execute(
        "SELECT id FROM nodes WHERE repo_id=? AND type='file' AND file_path='good.py'",
        (repo_id,),
    ).fetchone()[0]

    from core.graph.models import EdgeType
    both = store.get_neighbors(box_id, EdgeType.CONTAINS, direction="both")
    names = {n.name for n in both}
    # out: Box's own methods; in: the file that contains Box
    assert {"__init__", "get", "double"} <= names, f"missing methods in {names}"
    assert any(n.id == file_id for n in both), "file (the 'in' side) missing from both-direction result"


# ---------------- get_subgraph ----------------

def get_subgraph_is_induced_and_drops_unknown_ids():
    store, repo_id = _synth_store()
    double_id = _one(store, repo_id, "double", "good.py")
    get_id = _one(store, repo_id, "get", "good.py")
    box_id = _one(store, repo_id, "Box", "good.py")

    sub = store.get_subgraph([double_id, get_id, 999_999])
    node_ids = {n.id for n in sub.nodes}
    assert node_ids == {double_id, get_id}, \
        f"unknown id should be dropped silently, got {node_ids}"

    edge_pairs = {(e.source_node_id, e.target_node_id) for e in sub.edges}
    assert (double_id, get_id) in edge_pairs, "induced subgraph must include the CALLS edge"
    # Box is NOT in the requested id set, so the CONTAINS edge Box->double
    # must NOT appear even though double is in the set (induced = both ends
    # must be in node_ids).
    assert box_id not in {e.source_node_id for e in sub.edges}, \
        "edge whose other endpoint (Box) isn't in the requested set must be excluded"


def get_subgraph_empty_input_returns_empty():
    store, _repo_id = _synth_store()
    sub = store.get_subgraph([])
    assert sub.nodes == () and sub.edges == ()


# ---------------- search_symbols regression (qualified-name fix) ----------------

def search_symbols_still_ranks_helper_first_for_its_own_name():
    store, repo_id = _synth_store()
    results = store.search_symbols("helper", limit=5)
    assert results, "expected at least one result"
    assert results[0].name == "helper", f"expected helper first, got {results[0].name}"


def search_symbols_qualified_class_dot_method_query():
    store, repo_id = _synth_store()
    results = store.search_symbols("Box.get", limit=5)
    assert results, "expected at least one result"
    assert results[0].name == "get", \
        f"expected Box.get to surface first for 'Box.get' query, got {results[0].name}"


# ---------------- real repos (reuse step 4's debug DBs) ----------------

def requests_search_and_get_node_roundtrip():
    store = open_debug_db("debug_requests.db")
    results = store.search_symbols("Session.request", limit=5)
    assert results, "Session.request query returned nothing"
    top = results[0]
    assert top.name == "request" and top.file_path.endswith("requests/sessions.py"), \
        f"expected Session.request on top, got {top.name} ({top.file_path})"

    # round-trip: get_node on the id search_symbols just gave us
    fetched = store.get_node(top.id)
    assert fetched is not None and fetched.id == top.id and fetched.name == top.name

    print(f"        requests: 'Session.request' -> {top.name} "
          f"({top.file_path}:{top.start_line})")


def requests_get_neighbors_matches_known_call():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")
    req = store.search_symbols("Session.request", limit=1)[0]
    callees = store.get_neighbors(req.id, EdgeType.CALLS, direction="out")
    callee_names = {c.name for c in callees}
    assert "send" in callee_names, \
        f"Session.request's callees should include send, got {callee_names}"


def flask_exact_symbol_search():
    store = open_debug_db("debug_flask.db")
    results = store.search_symbols("Flask", limit=5)
    assert results, "Flask exact symbol query returned no results"
    assert results[0].name == "Flask", f"expected Flask first, got {results[0].name!r}"
    print(f"        flask: 'Flask' -> {results[0].name} "
          f"({results[0].file_path}:{results[0].start_line})")


# For manual verification
def requests_list_outgoing_calls():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")
    req = store.search_symbols("Session.request", limit=1)[0]

    callees = store.get_neighbors(req.id, EdgeType.CALLS, direction="out")

    print(f"\n        OUTGOING CALLS from {req.name}:")
    for callee in callees:
        print(
            f"          -> {callee.name} "
            f"({callee.file_path}:{callee.start_line})"
        )


def requests_list_incoming_calls():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")
    req = store.search_symbols("Session.request", limit=1)[0]

    callers = store.get_neighbors(req.id, EdgeType.CALLS, direction="in")

    print(f"\n        INCOMING CALLS to {req.name}:")
    for caller in callers:
        print(
            f"          <- {caller.name} "
            f"({caller.file_path}:{caller.start_line})"
        )

def requests_list_incoming_calls_to_dispatch_hook():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")

    hook = store.search_symbols("dispatch_hook", limit=1)[0]

    callers = store.get_neighbors(
        hook.id,
        EdgeType.CALLS,
        direction="in",
    )

    print(f"\n        INCOMING CALLS to {hook.name}:")

    for caller in callers:
        print(
            f"          <- {caller.name} "
            f"({caller.file_path}:{caller.start_line})"
        )

def requests_show_subgraph():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")

    request = store.search_symbols("Session.request", limit=1)[0]
    callees = store.get_neighbors(
        request.id,
        EdgeType.CALLS,
        direction="out",
    )

    send = next(c for c in callees if c.name == "send")

    subgraph = store.get_subgraph([request.id, send.id])

    print("\n        SUBGRAPH:")
    print("        Nodes:")
    for node in subgraph.nodes:
        print(
            f"          {node.id}: {node.name} "
            f"({node.file_path}:{node.start_line})"
        )

    print("        Edges:")
    for edge in subgraph.edges:
        print(
            f"          {edge.source_node_id} "
            f"--{edge.type.value}--> "
            f"{edge.target_node_id} "
            f"(resolved={edge.resolved})"
        )

def requests_list_cross_file_subgraph():
    from core.graph.models import EdgeType

    store = open_debug_db("debug_requests.db")

    send = store.search_symbols("Session.send", limit=1)[0]
    dispatch = store.search_symbols("dispatch_hook", limit=1)[0]

    sub = store.get_subgraph([send.id, dispatch.id])

    print("\n        CROSS-FILE SUBGRAPH:")
    print("        Nodes:")
    for node in sub.nodes:
        print(
            f"          {node.id}: {node.name} "
            f"({node.file_path}:{node.start_line})"
        )

    print("        Edges:")
    for edge in sub.edges:
        print(
            f"          {edge.source_node_id} --{edge.type.value}--> "
            f"{edge.target_node_id} (resolved={edge.resolved})"
        )

    assert {n.id for n in sub.nodes} == {send.id, dispatch.id}
    assert any(
        e.source_node_id == send.id
        and e.target_node_id == dispatch.id
        and e.type == EdgeType.CALLS
        for e in sub.edges
    )

def main() -> int:
    print("== CodeGraph Agent - step 6 smoke test ==")
    check("get_node: returns correct fields", get_node_returns_correct_fields)
    check("get_node: unknown id -> None", get_node_unknown_id_returns_none)
    check("get_neighbors: out/CALLS deduplicates repeated calls to same target",
          get_neighbors_out_calls_deduplicated)
    check("get_neighbors: in direction == callers",
          get_neighbors_in_direction_is_callers)
    check("get_neighbors: edge_type filter excludes other edge types",
          get_neighbors_edge_type_filter_excludes_other_types)
    check("get_neighbors: both direction unions out+in",
          get_neighbors_both_direction_unions_out_and_in)
    check("get_subgraph: induced, drops unknown ids, excludes out-of-set edges",
          get_subgraph_is_induced_and_drops_unknown_ids)
    check("get_subgraph: empty input -> empty result",
          get_subgraph_empty_input_returns_empty)
    check("search_symbols: bare name still ranks itself first",
          search_symbols_still_ranks_helper_first_for_its_own_name)
    check("search_symbols: 'Box.get' qualified-name query ranks Box.get first",
          search_symbols_qualified_class_dot_method_query)
    check("REAL requests: search_symbols('Session.request') + get_node round-trip",
          requests_search_and_get_node_roundtrip)
    check("REAL requests: get_neighbors(Session.request, CALLS, out) includes send",
          requests_get_neighbors_matches_known_call)
    check("REAL flask: search_symbols('Flask') ranks the Flask class first",
          flask_exact_symbol_search)

    # to do manual verification against the repo
    print("\nManual Verification")
    requests_list_outgoing_calls()
    requests_list_incoming_calls()
    requests_list_incoming_calls_to_dispatch_hook()
    requests_show_subgraph()
    requests_list_cross_file_subgraph()

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    if SKIPPED:
        print(f"\n{len(SKIPPED)} check(s) SKIPPED: {SKIPPED}")
    print("\nAll green. Step 6 done -- the frozen GraphStore query contract is "
          "complete. Everything above this line (agent, MCP tools, API) should "
          "now only ever touch get_node/get_neighbors/get_subgraph/search_symbols.")


    return 0


if __name__ == "__main__":
    sys.exit(main())