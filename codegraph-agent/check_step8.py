"""Step 8 smoke test. Run FROM PROJECT ROOT: python check_step8.py

Two layers, deliberately separated:
  1. core/graph/traversal.find_call_path -- pure graph logic, no LLM, no
     network. This is where "verify against a manually-traced path" is
     actually proven: every synthetic call chain below is small enough to
     trace by eye, and the assertions pin the exact expected path.
  2. FlowTracer.trace end-to-end (entry-point resolution + narration) --
     needs GROQ_API_KEY, skipped (not failed) if unset, same pattern as
     check_step7.py.

Real-repo check reuses .codegraph/debug_requests.db from check_step4.py
(skipped if missing) and re-verifies a fact already hand-confirmed in step
4/6: Session.request calls Session.send directly.

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


def require_groq():
    import os
    if not os.getenv("GROQ_API_KEY"):
        raise _Skip("GROQ_API_KEY not set")


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


# ---------------- synthetic fixture: hand-traceable call chains ----------------

CHAIN_SRC = '''\
"""synthetic module for step 8 flow tracing -- a manually-traceable chain,
a branch, a cycle, and a dead end."""


def step_a():
    return step_b()


def step_b():
    return step_c()


def step_c():
    return step_d()


def step_d():
    return 99


def cyclic():
    return cyclic_helper()


def cyclic_helper():
    return cyclic()


def dead_end():
    return 1


def calls_external():
    return undefined_thing()
'''


def make_synth_project(tmp: Path) -> None:
    (tmp / "chain.py").write_text(CHAIN_SRC, encoding="utf-8")


def _synth_store():
    proj_ctx = tempfile.TemporaryDirectory()
    db_ctx = tempfile.TemporaryDirectory()
    proj, dbdir = Path(proj_ctx.name), Path(db_ctx.name)
    make_synth_project(proj)
    store, repo_id = build(str(proj), dbdir / "graph.db", is_local=True)
    store._keepalive = (proj_ctx, db_ctx)  # keep tempdirs alive with the store
    return store, repo_id


# ---------------- layer 1: pure traversal, hand-traced paths ----------------

def goal_directed_shortest_path_through_chain():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    a, b, c = (_one(store, repo_id, n) for n in ("step_a", "step_b", "step_c"))

    path = find_call_path(store, a, goal_id=c)
    assert [n.id for n in path] == [a, b, c], \
        f"expected a->b->c, got {[n.name for n in path]}"


def greedy_walk_matches_hand_traced_chain():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    a, b, c, d = (_one(store, repo_id, n)
                  for n in ("step_a", "step_b", "step_c", "step_d"))

    path = find_call_path(store, a)  # no goal -> greedy walk to the dead end
    assert [n.id for n in path] == [a, b, c, d], \
        f"expected the full a->b->c->d chain (step_d is a dead end), got {[n.name for n in path]}"


def max_depth_bound_is_respected():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    a, b, c = (_one(store, repo_id, n) for n in ("step_a", "step_b", "step_c"))

    path = find_call_path(store, a, max_depth=2)  # 2 hops: a->b, b->c, stop before c->d
    assert [n.id for n in path] == [a, b, c], \
        f"expected exactly 2 hops (a,b,c), got {[n.name for n in path]}"


def cycle_terminates_without_looping():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    cyc, helper = (_one(store, repo_id, n) for n in ("cyclic", "cyclic_helper"))

    path = find_call_path(store, cyc)  # cyclic -> cyclic_helper -> cyclic (revisit, stop)
    assert [n.id for n in path] == [cyc, helper], \
        f"expected the cycle to stop after one loop back, got {[n.name for n in path]}"


def no_path_returns_empty():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    dead, c = _one(store, repo_id, "dead_end"), _one(store, repo_id, "step_c")

    assert find_call_path(store, dead, goal_id=c) == [], \
        "dead_end makes no calls -- there is no path to step_c"


def unknown_start_id_returns_empty():
    from core.graph.traversal import find_call_path

    store, _repo_id = _synth_store()
    assert find_call_path(store, 999_999) == []


def external_stub_start_returns_empty():
    from core.graph.traversal import find_call_path

    store, repo_id = _synth_store()
    stub_id = store._conn.execute(
        "SELECT id FROM nodes WHERE repo_id=? AND file_path='<external>' "
        "AND name='undefined_thing'", (repo_id,),
    ).fetchone()[0]
    assert find_call_path(store, stub_id) == [], \
        "an external stub node is a dead end by construction, never a valid start"


# ---------------- layer 2: FlowTracer end-to-end (needs GROQ_API_KEY) ----------------

def _make_tracer(store):
    require_groq()
    from adapters.llm.groq_provider import GroqProvider
    from core.agent.flow_tracer import FlowTracer
    return FlowTracer(store, GroqProvider())


def flow_tracer_goal_directed_end_to_end():
    store, repo_id = _synth_store()
    tracer = _make_tracer(store)

    result = tracer.trace("step_a", target="step_c")
    names = [n["name"] for n in result["nodes"]]
    assert names == ["step_a", "step_b", "step_c"], f"unexpected path: {names}"
    assert len(result["edges"]) == 2, f"expected 2 consecutive edges, got {result['edges']}"
    assert [s["node_id"] for s in result["steps"]] == [n["id"] for n in result["nodes"]], \
        "narration steps must be in the same order, tied 1:1 to path nodes"
    for step in result["steps"]:
        assert step["text"], "every step must have non-empty narration text"
    print(f"        goal-directed trace: {' -> '.join(names)}")
    for step in result["steps"]:
        print(f"          [{step['node_id']}] {step['text']}")


def flow_tracer_resolves_natural_language_query():
    store, repo_id = _synth_store()
    tracer = _make_tracer(store)

    result = tracer.trace("What happens when step_a runs?")
    assert result["nodes"], "expected a non-empty trace"
    assert result["nodes"][0]["name"] == "step_a", \
        f"expected entry resolution to find step_a, got {result['nodes'][0]['name']}"


def flow_tracer_no_match_returns_error_not_crash():
    store, repo_id = _synth_store()
    tracer = _make_tracer(store)

    result = tracer.trace("zzz_totally_unrelated_nonsense_zzz")
    assert result["nodes"] == [] and result["edges"] == [] and result["steps"] == []
    assert "error" in result and result["error"]


# ---------------- real repo: re-verify a hand-confirmed fact ----------------

def requests_session_request_to_send_is_one_hop():
    store = open_debug_db("debug_requests.db")
    tracer = _make_tracer(store)

    result = tracer.trace("Session.request", target="Session.send")
    names = [n["name"] for n in result["nodes"]]
    assert names == ["request", "send"], \
        f"Session.request calls Session.send directly (confirmed in step 4/6) -- got {names}"
    assert len(result["edges"]) == 1 and result["edges"][0]["resolved"] is True
    print(f"        requests: Session.request -> Session.send, 1 hop, resolved=True")
    for step in result["steps"]:
        print(f"          [{step['node_id']}] {step['text']}")


def main() -> int:
    print("== CodeGraph Agent - step 8 smoke test ==")
    print("-- layer 1: pure traversal (no LLM, hand-traced paths) --")
    check("goal-directed BFS finds the exact hand-traced a->b->c path",
          goal_directed_shortest_path_through_chain)
    check("greedy walk (no goal) matches the full hand-traced chain",
          greedy_walk_matches_hand_traced_chain)
    check("max_depth bound stops the walk exactly where expected",
          max_depth_bound_is_respected)
    check("a call cycle terminates instead of looping forever",
          cycle_terminates_without_looping)
    check("no path between unconnected functions -> []",
          no_path_returns_empty)
    check("unknown start id -> []",
          unknown_start_id_returns_empty)
    check("an external stub node can never be a trace start -> []",
          external_stub_start_returns_empty)

    print("-- layer 2: FlowTracer end-to-end (needs GROQ_API_KEY) --")
    check("goal-directed trace end-to-end: nodes+edges+narration all line up",
          flow_tracer_goal_directed_end_to_end)
    check("natural-language query resolves to the right entry point",
          flow_tracer_resolves_natural_language_query)
    check("no matching symbol -> graceful error, not a crash",
          flow_tracer_no_match_returns_error_not_crash)

    print("-- real repo (reuses debug_requests.db) --")
    check("REAL requests: Session.request -> Session.send is a 1-hop trace",
          requests_session_request_to_send_is_one_hop)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    if SKIPPED:
        print(f"\n{len(SKIPPED)} check(s) SKIPPED: {SKIPPED}")
    print("\nAll green. Step 8 done. Next: step 9 (Evidence UI -- chat + citation panel).")
    return 0


if __name__ == "__main__":
    sys.exit(main())