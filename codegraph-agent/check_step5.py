"""Step 5 smoke test. Run FROM PROJECT ROOT: python check_step5.py

Verifies BM25 symbol/text retrieval:
- exact symbol-name matching is strongly boosted
- prefix/substring name matching works
- snake_case and camelCase identifiers tokenize correctly
- prose/docstring/source-text queries retrieve relevant nodes
- unrelated queries return no arbitrary results
- limit is respected
- external stub nodes are never indexed
- BM25 index is lazy and invalidated after add_node()

Real-repo checks reuse the persistent Step 4 debug DBs:

    sqlite3 .codegraph/debug_requests.db
    sqlite3 .codegraph/debug_flask.db

Exit code 0 = green.
"""

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


def q(store, sql, params=()):
    return store._conn.execute(sql, params).fetchall()


def node_by_id(store, node_id):
    row = q(
        store,
        """
        SELECT id, repo_id, type, name, file_path, start_line,
               end_line, source_text, docstring
        FROM nodes
        WHERE id=?
        """,
        (node_id,),
    )[0]

    from core.graph.models import Node, NodeType

    return Node(
        id=row[0],
        repo_id=row[1],
        type=NodeType(row[2]),
        name=row[3],
        file_path=row[4],
        start_line=row[5],
        end_line=row[6],
        source_text=row[7],
        docstring=row[8],
    )


# ---------------------------------------------------------------------------
# Synthetic corpus
# ---------------------------------------------------------------------------

def make_synthetic_store():
    from adapters.storage.sqlite_store import SqliteGraphStore
    from core.graph.models import Node, NodeType

    tmp = tempfile.TemporaryDirectory()
    store = SqliteGraphStore(str(Path(tmp.name) / "graph.db"))

    repo_id = store.add_repo(
        "synthetic",
        "synthetic",
        tmp.name,
    )

    nodes = [
        Node(
            id=None,
            repo_id=repo_id,
            type=NodeType.FUNCTION,
            name="send_email",
            file_path="mail.py",
            start_line=1,
            end_line=4,
            source_text="def send_email(user): send the email notification",
            docstring="Send an email notification to the user.",
        ),
        Node(
            id=None,
            repo_id=repo_id,
            type=NodeType.FUNCTION,
            name="HTTPAdapter",
            file_path="http.py",
            start_line=1,
            end_line=4,
            source_text="class HTTPAdapter: handles HTTP requests",
            docstring="Adapter for HTTP connections.",
        ),
        Node(
            id=None,
            repo_id=repo_id,
            type=NodeType.FUNCTION,
            name="get_neighbors",
            file_path="graph.py",
            start_line=1,
            end_line=4,
            source_text="def get_neighbors(node_id): return adjacent nodes",
            docstring="Return neighboring graph nodes.",
        ),
        Node(
            id=None,
            repo_id=repo_id,
            type=NodeType.FUNCTION,
            name="unrelated",
            file_path="other.py",
            start_line=1,
            end_line=2,
            source_text="def unrelated(): calculate something about weather",
            docstring="Weather calculation.",
        ),
        # External node must never enter the BM25 corpus.
        Node(
            id=None,
            repo_id=repo_id,
            type=NodeType.FUNCTION,
            name="send_email",
            file_path="<external>",
            start_line=0,
            end_line=0,
            source_text="",
            docstring="",
        ),
    ]

    ids = [store.add_node(node) for node in nodes]
    return tmp, store, repo_id, ids


def synth_exact_name_is_boosted():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("send_email", limit=10)

        assert results, "exact symbol query returned no results"
        assert results[0].name == "send_email"
        assert results[0].file_path == "mail.py", \
            "real source node should beat external stub"
    finally:
        tmp.cleanup()


def synth_prose_query_uses_bm25():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols(
            "send an email notification to the user",
            limit=10,
        )

        assert results, "prose query returned no results"
        assert results[0].name == "send_email", \
            f"expected send_email first, got {results[0].name!r}"
    finally:
        tmp.cleanup()


def synth_camel_case_tokenization():
    from core.retrieval.bm25_retriever import tokenize

    assert tokenize("HTTPAdapter") == ["http", "adapter"]
    assert tokenize("get_neighbors") == ["get", "neighbors"]


def synth_camel_case_query_matches_identifier():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("http adapter", limit=10)

        assert results, "HTTP adapter query returned no results"
        assert results[0].name == "HTTPAdapter"
    finally:
        tmp.cleanup()


def synth_snake_case_query_matches_identifier():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("get neighbors", limit=10)

        assert results, "get neighbors query returned no results"
        assert results[0].name == "get_neighbors"
    finally:
        tmp.cleanup()


def synth_limit_is_respected():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("node", limit=2)
        assert len(results) <= 2
    finally:
        tmp.cleanup()


def synth_unrelated_query_returns_nothing():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("quantum banana spaceship", limit=10)
        assert results == [], f"expected no lexical matches, got {results}"
    finally:
        tmp.cleanup()


def synth_external_stub_is_not_returned():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        results = store.search_symbols("send_email", limit=10)

        assert all(node.file_path != "<external>" for node in results)
    finally:
        tmp.cleanup()


def synth_index_is_lazy():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        assert store._bm25 is None, "BM25 should not be built during construction"

        store.search_symbols("send_email")

        assert store._bm25 is not None, \
            "first search should lazily build the BM25 index"
    finally:
        tmp.cleanup()


def synth_index_invalidates_after_add_node():
    tmp, store, repo_id, ids = make_synthetic_store()
    try:
        store.search_symbols("send_email")
        assert store._bm25 is not None

        from core.graph.models import Node, NodeType

        store.add_node(
            Node(
                id=None,
                repo_id=repo_id,
                type=NodeType.FUNCTION,
                name="new_function",
                file_path="new.py",
                start_line=1,
                end_line=2,
                source_text="def new_function():",
                docstring="A newly added function.",
            )
        )

        assert store._bm25 is None, \
            "add_node must invalidate the cached BM25 index"

        results = store.search_symbols("new_function")
        assert results
        assert results[0].name == "new_function"
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Real repositories
# ---------------------------------------------------------------------------

def open_debug_db(name):
    from adapters.storage.sqlite_store import SqliteGraphStore

    path = DEBUG_DB_DIR / name

    assert path.exists(), \
        f"{path} does not exist; run check_step4.py first"

    return SqliteGraphStore(str(path))


def real_requests_exact_symbol():
    store = open_debug_db("debug_requests.db")

    results = store.search_symbols("Session.request", limit=10)

    assert results, "Session.request returned no results"

    top = results[0]

    print(
        f"        requests request query -> "
        f"{top.name} ({top.file_path}:{top.start_line})"
    )

    assert top.name == "request"


def real_requests_send_symbol():
    store = open_debug_db("debug_requests.db")

    results = store.search_symbols("Session.send", limit=10)

    assert results, "Session.send returned no results"

    matches = [
        node for node in results
        if node.name == "send"
        and node.file_path.endswith("requests/sessions.py")
    ]

    assert matches, \
        "Session.send query did not surface requests/sessions.py:send"

    print(
        f"        requests send query -> "
        f"{matches[0].name} ({matches[0].file_path}:{matches[0].start_line})"
    )


def real_requests_prose_query():
    store = open_debug_db("debug_requests.db")

    results = store.search_symbols(
        "send HTTP request",
        limit=10,
    )

    assert results, "prose HTTP request query returned no results"

    print(
        f"        requests prose query -> "
        f"{results[0].name} ({results[0].file_path}:{results[0].start_line})"
    )


def real_flask_exact_symbol():
    store = open_debug_db("debug_flask.db")

    results = store.search_symbols("Flask", limit=5)

    assert results, "Flask exact symbol query returned no results"
    assert results[0].name == "Flask", \
        f"expected Flask first, got {results[0].name!r}"

    print(
        f"        flask exact query -> "
        f"{results[0].name} ({results[0].file_path}:{results[0].start_line})"
    )


def real_flask_prose_query():
    store = open_debug_db("debug_flask.db")

    results = store.search_symbols(
        "register a blueprint with the application",
        limit=10,
    )

    assert results, "Flask prose query returned no results"

    print(
        f"        flask prose query -> "
        f"{results[0].name} ({results[0].file_path}:{results[0].start_line})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("== CodeGraph Agent - step 5 smoke test ==")

    check(
        "synthetic: exact symbol name is strongly boosted",
        synth_exact_name_is_boosted,
    )
    check(
        "synthetic: prose query retrieves relevant text via BM25",
        synth_prose_query_uses_bm25,
    )
    check(
        "synthetic: camelCase/PascalCase tokenization",
        synth_camel_case_tokenization,
    )
    check(
        "synthetic: camelCase identifier matches natural-language query",
        synth_camel_case_query_matches_identifier,
    )
    check(
        "synthetic: snake_case identifier matches natural-language query",
        synth_snake_case_query_matches_identifier,
    )
    check(
        "synthetic: limit is respected",
        synth_limit_is_respected,
    )
    check(
        "synthetic: unrelated query returns no arbitrary results",
        synth_unrelated_query_returns_nothing,
    )
    check(
        "synthetic: external stubs are excluded from retrieval",
        synth_external_stub_is_not_returned,
    )
    check(
        "synthetic: BM25 index is built lazily",
        synth_index_is_lazy,
    )
    check(
        "synthetic: add_node invalidates cached BM25 index",
        synth_index_invalidates_after_add_node,
    )

    check(
        "REAL requests: exact Session.request retrieval",
        real_requests_exact_symbol,
    )
    check(
        "REAL requests: Session.send retrieval",
        real_requests_send_symbol,
    )
    check(
        "REAL requests: prose retrieval",
        real_requests_prose_query,
    )
    check(
        "REAL flask: exact Flask retrieval",
        real_flask_exact_symbol,
    )
    check(
        "REAL flask: prose retrieval",
        real_flask_prose_query,
    )

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1

    print("\nAll green. Step 5 done. Next: step 6 (graph read/query API).")
    return 0


if __name__ == "__main__":
    sys.exit(main())