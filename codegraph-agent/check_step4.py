"""Step 4 smoke test. Run FROM PROJECT ROOT: python check_step4.py
Reuses already-cloned psf/requests / pallets/flask (clone is idempotent).

Real-repo checks write to PERSISTENT db files under .codegraph/ (deleted and
rebuilt fresh each run) specifically so you can open them yourself afterwards:

    sqlite3 .codegraph/debug_requests.db
    sqlite3 .codegraph/debug_flask.db

Synthetic-project checks still use throwaway tempdirs -- nothing there is
worth inspecting by hand, the assertions already pin every value.

Exit code 0 = green."""

import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []
PROJECT_ROOT = Path(__file__).resolve().parent
DEBUG_DB_DIR = PROJECT_ROOT / ".codegraph"


def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
        traceback.print_exc()


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


def fresh_debug_db(name: str) -> Path:
    DEBUG_DB_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DB_DIR / name
    path.unlink(missing_ok=True)
    return path


class _LocalSource:
    """RepoSource stand-in for the synthetic on-disk project (no cloning)."""

    def clone(self, url: str) -> str:
        return url


# ---------------- SQL helpers (store's own read API isn't built until step 6) ----------------

def q(store, sql, params=()):
    return store._conn.execute(sql, params).fetchall()


def nodes_named(store, repo_id, name, file_path=None):
    if file_path is None:
        return q(store, "SELECT id, file_path, start_line, type FROM nodes "
                         "WHERE repo_id=? AND name=?", (repo_id, name))
    return q(store, "SELECT id, file_path, start_line, type FROM nodes "
                     "WHERE repo_id=? AND name=? AND file_path=?",
             (repo_id, name, file_path))


def edges_from(store, repo_id, source_id, edge_type):
    return q(store, "SELECT target_node_id, resolved FROM edges "
                     "WHERE repo_id=? AND source_node_id=? AND type=?",
             (repo_id, source_id, edge_type))


def dump_matches(store, repo_id, name):
    rows = nodes_named(store, repo_id, name)
    print(f"        DEBUG: {len(rows)} node(s) named {name!r} in repo_id={repo_id}:")
    for node_id, file_path, start_line, typ in rows:
        print(f"          id={node_id}  type={typ}  {file_path}:{start_line}")


# ---------------- synthetic project: known calls, hand-verifiable ----------------

GOOD_SRC = '''\
"""synthetic module for step 4"""
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


def synth_same_file_call_resolves():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id = build(proj, Path(dbdir) / "graph.db", is_local=True)

        [(caller_id, *_)] = nodes_named(store, repo_id, "caller", "good.py")
        [(helper_id, *_)] = nodes_named(store, repo_id, "helper", "good.py")

        calls = edges_from(store, repo_id, caller_id, "calls")
        targets = {t: r for t, r in calls}
        assert helper_id in targets and targets[helper_id] == 1, \
            "caller -> helper should be a single resolved CALLS edge"
        assert len(calls) == 2, f"expected 2 CALLS edges (helper + dynamic), got {len(calls)}"


def synth_unresolved_call_kept_as_external_stub():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id = build(proj, Path(dbdir) / "graph.db", is_local=True)

        [(caller_id, *_)] = nodes_named(store, repo_id, "caller", "good.py")
        calls = edges_from(store, repo_id, caller_id, "calls")
        unresolved = [(t, r) for t, r in calls if r == 0]
        assert len(unresolved) == 1, f"expected exactly 1 unresolved edge, got {len(unresolved)}"
        target_id = unresolved[0][0]
        row = q(store, "SELECT name, file_path FROM nodes WHERE id=?", (target_id,))[0]
        assert row[0] == "undefined_dynamic_thing"
        assert row[1] == "<external>", "unresolved call must be kept, pointing at a stub node"


def synth_self_call_resolves_within_class():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id = build(proj, Path(dbdir) / "graph.db", is_local=True)

        [(double_id, *_)] = nodes_named(store, repo_id, "double", "good.py")
        [(get_id, *_)] = nodes_named(store, repo_id, "get", "good.py")

        calls = edges_from(store, repo_id, double_id, "calls")
        assert len(calls) == 2, "double() calls self.get() twice"
        assert all(t == get_id and r == 1 for t, r in calls), \
            "both self.get() calls must resolve to Box.get, resolved=True"


def synth_cross_file_import_call_resolves():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id = build(proj, Path(dbdir) / "graph.db", is_local=True)

        [(caller_id, *_)] = nodes_named(store, repo_id, "uses_cross_file", "good.py")
        [(target_id, *_)] = nodes_named(store, repo_id, "other_func", "other.py")

        calls = edges_from(store, repo_id, caller_id, "calls")
        assert calls == [(target_id, 1)], \
            f"expected uses_cross_file -> other.other_func resolved, got {calls}"


def synth_contains_hierarchy():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id = build(proj, Path(dbdir) / "graph.db", is_local=True)

        [(file_id, *_)] = q(store, "SELECT id FROM nodes WHERE repo_id=? AND "
                                    "type='file' AND file_path='good.py'", (repo_id,))
        [(box_id, *_)] = nodes_named(store, repo_id, "Box", "good.py")
        [(init_id, *_)] = nodes_named(store, repo_id, "__init__", "good.py")

        file_children = {t for t, _ in edges_from(store, repo_id, file_id, "contains")}
        assert box_id in file_children, "file should CONTAIN class Box"

        box_children = {t for t, _ in edges_from(store, repo_id, box_id, "contains")}
        assert init_id in box_children, "class Box should CONTAIN its __init__"


def idempotent_reindex_returns_same_repo_id():
    with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as dbdir:
        make_synth_project(Path(proj))
        store, repo_id_1 = build(proj, Path(dbdir) / "graph.db", is_local=True)
        from core.graph.graph_builder import GraphBuilder
        from adapters.parsing.python_parser import PythonParser
        repo_id_2 = GraphBuilder(_LocalSource(), PythonParser(), store).index_repo(proj)
        assert repo_id_1 == repo_id_2, "re-indexing the same url must not duplicate the repo"
        node_count = q(store, "SELECT COUNT(*) FROM nodes WHERE repo_id=?", (repo_id_1,))[0][0]
        assert node_count > 0


# ---------------- real repos ----------------

def requests_known_call():
    db_path = fresh_debug_db("debug_requests.db")
    store, repo_id = build("psf/requests", db_path)
    print(f"        wrote {db_path}  (repo_id={repo_id}) -- inspect with: sqlite3 {db_path}")

    req = [h for h in nodes_named(store, repo_id, "request")
           if h[1].endswith("requests/sessions.py")]
    if len(req) != 1:
        dump_matches(store, repo_id, "request")
    assert len(req) == 1, f"Session.request not found uniquely: {req}"
    request_id = req[0][0]

    # # Give me the send in sessions.py
    # send = [h for h in nodes_named(store, repo_id, "send")
    #         if h[1].endswith("requests/sessions.py")]
    # if len(send) != 1:
    #     dump_matches(store, repo_id, "send")
    # assert len(send) == 1, f"Session.send not found uniquely: {send}"
    # send_id = send[0][0]

    # Give me the send whose parent is the Session class.
    send_candidates = [
        h for h in nodes_named(store, repo_id, "send")
        if h[1].endswith("requests/sessions.py")
    ]

    # Find the send node contained by the Session class.
    session_candidates = [
        h for h in nodes_named(store, repo_id, "Session")
        if h[1].endswith("requests/sessions.py")
    ]
    assert len(session_candidates) == 1, \
        f"Session class not found uniquely: {session_candidates}"
    session_id = session_candidates[0][0]

    send = []
    for send_node in send_candidates:
        send_id_candidate = send_node[0]
        parents = q(store, """
            SELECT source_node_id
            FROM edges
            WHERE repo_id=? AND target_node_id=? AND type='contains'
        """, (repo_id, send_id_candidate))
        if any(parent_id == session_id for (parent_id,) in parents):
            send.append(send_node)

    assert len(send) == 1, f"Session.send not found uniquely: {send}"
    send_id = send[0][0]    

    calls = edges_from(store, repo_id, request_id, "calls")
    targets = {t: r for t, r in calls}
    if not (send_id in targets and targets[send_id] == 1):
        print(f"        DEBUG: Session.request's CALLS edges (target_id, resolved): {calls}")
    assert send_id in targets and targets[send_id] == 1, \
        "Session.request must have a resolved self-call CALLS edge to Session.send"

    _sanity_check_edge_contract(store, repo_id)
    _report_resolution_stats(store, repo_id, "requests")


def flask_messy_repo_call_resolution():
    db_path = fresh_debug_db("debug_flask.db")
    store, repo_id = build("pallets/flask", db_path)
    print(f"        wrote {db_path}  (repo_id={repo_id}) -- inspect with: sqlite3 {db_path}")

    _sanity_check_edge_contract(store, repo_id)
    n_calls, n_resolved = _report_resolution_stats(store, repo_id, "flask")
    assert n_calls > 200, "too few CALLS edges extracted from a decorator-heavy repo"
    # Deliberately NOT asserting a resolution-rate threshold here: flask leans
    # heavily on werkzeug/jinja2/click plus stdlib/builtins for a large share
    # of its calls, all genuinely outside this repo, so a low in-repo
    # resolution rate can be *correct*, not a bug. Eyeball the printed
    # top-unresolved-names list above instead -- real bugs look like known
    # in-repo names showing up unresolved, not like `len`, `isinstance`,
    # `dict`, or werkzeug/click symbols showing up unresolved.
    assert n_resolved > 0, "zero resolved calls in the whole repo is a red flag"

    n_contains = q(store, "SELECT COUNT(*) FROM edges WHERE repo_id=? AND type='contains'",
                   (repo_id,))[0][0]
    assert n_contains > 200, "too few CONTAINS edges (hierarchy not being wired)"


def _report_resolution_stats(store, repo_id, label):
    n_calls = q(store, "SELECT COUNT(*) FROM edges WHERE repo_id=? AND type='calls'",
                (repo_id,))[0][0]
    n_resolved = q(store, "SELECT COUNT(*) FROM edges WHERE repo_id=? AND type='calls' "
                           "AND resolved=1", (repo_id,))[0][0]
    pct = 100 * n_resolved // max(n_calls, 1)
    print(f"        {label}: {n_calls} CALLS edges, {n_resolved} resolved ({pct}%)")

    top_unresolved = q(store, """
        SELECT n.name, COUNT(*) c FROM edges e
        JOIN nodes n ON n.id = e.target_node_id
        WHERE e.repo_id=? AND e.type='calls' AND e.resolved=0
        GROUP BY n.name ORDER BY c DESC LIMIT 10
    """, (repo_id,))
    print(f"        {label}: top unresolved call names (should look like "
          f"builtins/stdlib/deps, not in-repo symbols):")
    for name, cnt in top_unresolved:
        print(f"          {cnt:>4}  {name}")
    return n_calls, n_resolved


def _sanity_check_edge_contract(store, repo_id):
    """resolved=True edges must point at real source; resolved=False must
    point at the external stub -- never mixed up."""
    bad_resolved = q(store, """
        SELECT e.id FROM edges e JOIN nodes n ON n.id = e.target_node_id
        WHERE e.repo_id=? AND e.type='calls' AND e.resolved=1
          AND n.file_path='<external>'
    """, (repo_id,))
    assert not bad_resolved, "a resolved=True CALLS edge points at an external stub"

    bad_unresolved = q(store, """
        SELECT e.id FROM edges e JOIN nodes n ON n.id = e.target_node_id
        WHERE e.repo_id=? AND e.type='calls' AND e.resolved=0
          AND n.file_path != '<external>'
    """, (repo_id,))
    assert not bad_unresolved, "a resolved=False CALLS edge points at real source, not a stub"


def main() -> int:
    print("== CodeGraph Agent - step 4 smoke test ==")
    check("synthetic: same-file call resolves (caller -> helper)",
          synth_same_file_call_resolves)
    check("synthetic: unresolved call kept as external stub, not dropped",
          synth_unresolved_call_kept_as_external_stub)
    check("synthetic: self.get() resolves within class (Box.double -> Box.get)",
          synth_self_call_resolves_within_class)
    check("synthetic: cross-file relative-import call resolves",
          synth_cross_file_import_call_resolves)
    check("synthetic: CONTAINS hierarchy (file->Box->__init__)",
          synth_contains_hierarchy)
    check("re-indexing same url is idempotent (no duplicate repo row)",
          idempotent_reindex_returns_same_repo_id)
    check("REAL requests: Session.request -> Session.send resolves + edge contract",
          requests_known_call)
    check("REAL flask (messy): call resolution + CONTAINS hierarchy sanity",
          flask_messy_repo_call_resolution)
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("\nAll green. Step 4 done. Next: step 5 "
          "(BM25 index over node text).")
    return 0


if __name__ == "__main__":
    sys.exit(main())