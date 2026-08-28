"""Step 7 smoke test. Run FROM PROJECT ROOT: python check_step7.py
Requires GROQ_API_KEY in the environment (free tier: https://console.groq.com).
Reuses .codegraph/debug_requests.db and .codegraph/debug_flask.db written by
check_step4.py -- run that first if they're missing (skipped, not failed,
otherwise).

This test can't assert exact wording (LLM output isn't deterministic even at
temperature=0 across days/model updates) -- it asserts the GROUNDING
guarantees that are actually load-bearing: every returned chunk cites at
least one node id, every cited id resolves to a real node via get_node, and
prints the resolved file:line + text for each chunk so you can eyeball
whether the citations actually support the claim. That eyeballing is the
"verify grounded citations on 5+ hand-written questions" step -- read the
printed output, not just the PASS/FAIL.

Exit code 0 = green (mechanically grounded); read the printed evidence
yourself to judge semantic correctness.
"""

import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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


def open_debug_db(name: str):
    from adapters.storage.sqlite_store import SqliteGraphStore

    path = DEBUG_DB_DIR / name
    if not path.exists():
        raise _Skip(f"{path} not found -- run check_step4.py first")
    return SqliteGraphStore(str(path))


def make_agent():
    import os

    from adapters.llm.groq_provider import GroqProvider
    from core.agent.qa_agent import QAAgent

    if not os.getenv("GROQ_API_KEY"):
        raise _Skip("GROQ_API_KEY not set")
    llm = GroqProvider()

    def build(db_name):
        store = open_debug_db(db_name)
        return QAAgent(store, llm), store

    return build


def _print_answer(label, question, chunks):
    print(f"        [{label}] Q: {question}")
    if not chunks:
        print("          -> (no grounded answer)")
        return
    for chunk in chunks:
        print(f"          - {chunk['text']}")
        print(f"            cites node_ids: {chunk['node_ids']}")


def assert_grounded(store, chunks, question, label):
    """The mechanical guarantee: every chunk has >=1 node_id, every id
    resolves via get_node, and (bonus) print the actual file:line for each
    cited id so a human can judge relevance, not just resolvability."""
    assert chunks, f"[{label}] expected a grounded answer for: {question!r}"
    for chunk in chunks:
        assert chunk.get("node_ids"), f"[{label}] chunk has no node_ids: {chunk}"
        for node_id in chunk["node_ids"]:
            node = store.get_node(node_id)
            assert node is not None, \
                f"[{label}] chunk cites node_id={node_id} which does not resolve"
    _print_answer(label, question, chunks)
    for chunk in chunks:
        for node_id in chunk["node_ids"]:
            node = store.get_node(node_id)
            print(f"              -> {node.name} ({node.file_path}:{node.start_line})")


# ---------------- hand-written questions (5+ required) ----------------

def requests_session_request_purpose():
    build = make_agent()
    agent, store = build("debug_requests.db")
    q = "What does the Session.request method do?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "requests")


def requests_request_calls_send():
    build = make_agent()
    agent, store = build("debug_requests.db")
    q = "Which method does Session.request call to actually send the prepared request?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "requests")
    cited_names = {
        store.get_node(nid).name
        for chunk in chunks for nid in chunk["node_ids"]
    }
    assert "send" in cited_names, \
        f"expected 'send' among cited symbols, got {cited_names}"


def requests_http_adapter_purpose():
    build = make_agent()
    agent, store = build("debug_requests.db")
    q = "What is the purpose of the HTTPAdapter class?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "requests")


def requests_session_class_purpose():
    build = make_agent()
    agent, store = build("debug_requests.db")
    q = "What does the Session class represent?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "requests")


def flask_class_purpose():
    build = make_agent()
    agent, store = build("debug_flask.db")
    q = "What does the Flask class represent?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "flask")


def flask_wsgi_app_purpose():
    build = make_agent()
    agent, store = build("debug_flask.db")
    q = "What does the wsgi_app method do?"
    chunks = agent.answer(q)
    assert_grounded(store, chunks, q, "flask")


def no_evidence_question_returns_empty_without_hallucinating():
    """Deliberately unrelated to any code in the repo. search_symbols should
    find nothing with lexical overlap, so _gather_context returns [] and the
    agent never even calls the LLM -- deterministic, not a vibes check."""
    build = make_agent()
    agent, _store = build("debug_requests.db")
    q = "What is the airspeed velocity of an unladen swallow?"
    chunks = agent.answer(q)
    assert chunks == [], f"expected no grounded answer for an out-of-scope question, got {chunks}"
    print(f"        [requests] Q: {q}\n          -> (correctly) no grounded answer, no LLM call made")


def main() -> int:
    print("== CodeGraph Agent - step 7 smoke test ==")
    check("requests: Session.request purpose", requests_session_request_purpose)
    check("requests: Session.request -> send (cites 'send')", requests_request_calls_send)
    check("requests: HTTPAdapter purpose", requests_http_adapter_purpose)
    check("requests: Session class purpose", requests_session_class_purpose)
    check("flask: Flask class purpose", flask_class_purpose)
    check("flask: wsgi_app purpose", flask_wsgi_app_purpose)
    check("out-of-scope question -> empty, no hallucination",
          no_evidence_question_returns_empty_without_hallucinating)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    if SKIPPED:
        print(f"\n{len(SKIPPED)} check(s) SKIPPED: {SKIPPED}")
    print("\nAll green (mechanically grounded). Now READ the printed Q/A/citations "
          "above and judge relevance yourself -- that's the actual verification step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())