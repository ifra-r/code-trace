"""FastAPI layer — thin wiring only (design doc §6).
If you're writing logic here, it belongs in /core instead.

Step 2: /health + /clone.
Step 10 (this revision): /index wired to the real pipeline (clone -> parse ->
resolve -> SQLite -> BM25, steps 3-6), and /ask wired to QAAgent (step 7).

DB PATH CONVENTION (established here, since nothing upstream fixed one):
one SQLite file per indexed repo at .codegraph/dbs/<repo_name>/graph.db,
where <repo_name> is the same name GitHubScanner.clone() uses for its own
.codegraph/repos/<repo_name> clone directory -- a sibling location, not
nested inside the git clone itself. Keep mcp_server/server.py's
CODEGRAPH_DB_PATH pointed at the specific file for the repo you want it to
serve; that layer wasn't built to juggle multiple repos.

KNOWN LIMITATION: GraphBuilder.index_repo()'s idempotency check (step 4) and
this file's "already indexed?" check both key off the exact source_url
string the caller passes -- "psf/requests" and
"https://github.com/psf/requests" are different keys even though they
resolve to the same clone. As long as the frontend always carries forward
the canonical source_url returned by /index (which it does -- see
frontend/src/pages/LandingPage.jsx), this doesn't bite in the normal
index-once-then-ask flow. Typing a differently-formatted URL for an
already-indexed repo would index it a second time into the same db file
under a new repo_id, since get_node/get_neighbors/search_symbols don't
filter by repo_id (pre-existing from steps 5-6, not new here). Worth fixing
with URL normalization if this becomes a real workflow, not just a manual
demo.
"""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from adapters.parsing.python_parser import PythonParser
from adapters.storage.sqlite_store import SqliteGraphStore
from adapters.vcs.github_scanner import GitHubScanner
from core.agent.flow_tracer import FlowTracer
from core.agent.qa_agent import QAAgent
from core.graph.graph_builder import GraphBuilder

app = FastAPI(title="CodeTrace")

# Belt-and-suspenders: the frontend normally talks through Vite's /api proxy
# (see frontend/vite.config.js), so CORS shouldn't trigger — but this covers
# direct browser calls to :8000 while debugging.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

scanner = GitHubScanner()
_DB_ROOT = Path(".codegraph/dbs")
_llm_provider = None  # lazy-constructed -- see _get_llm


class RepoRequest(BaseModel):
    url: str


class IndexRequest(BaseModel):
    url: str
    force: bool = False


class AskRequest(BaseModel):
    url: str
    question: str


class TraceRequest(BaseModel):
    url: str
    query: str
    target: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/clone")
def clone_repo(req: RepoRequest):
    """Clone (or reuse) a GitHub repo locally. Returns name + local_path."""
    try:
        local_path = scanner.clone(req.url)
    except ValueError as exc:          # unparseable URL
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:        # git/network failure
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"name": Path(local_path).name, "local_path": local_path}


@app.post("/index")
def index_repo(req: IndexRequest):
    """Index a repo (clone -> parse -> resolve calls -> SQLite -> BM25).

    If this exact url was already indexed and force=False, does NOT
    re-index -- returns status="already_indexed" with the existing repo's
    stats so the caller (the frontend's confirm-reindex prompt) can decide
    whether to call again with force=True.
    """
    try:
        local_path = scanner.clone(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db_path = _db_path_for(Path(local_path).name)

    if db_path.exists() and not req.force:
        store = SqliteGraphStore(str(db_path))
        existing = store.get_repo_by_url(req.url)
        if existing is not None:
            return {
                "status": "already_indexed",
                "repo": _repo_payload(existing),
                **_counts(store, existing.id),
            }
        # db file exists (e.g. from a differently-formatted url variant --
        # see module docstring) but has no row for THIS exact url -- fall
        # through and index fresh into the same file rather than erroring.

    if req.force and db_path.exists():
        # No delete-repo method exists in the frozen GraphStore contract,
        # and doesn't need one: one file per repo means "start over" is
        # just "remove the file," not a new schema operation.
        db_path.unlink()

    store = SqliteGraphStore(str(db_path))
    try:
        repo_id = GraphBuilder(scanner, PythonParser(), store).index_repo(req.url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"indexing failed: {exc}") from exc

    repo = store.get_repo_by_url(req.url)
    return {
        "status": "indexed",
        "repo": _repo_payload(repo),
        **_counts(store, repo_id),
    }


@app.post("/ask")
def ask(req: AskRequest):
    """Q&A over an already-indexed repo. Returns evidence.py's validated
    [{text, node_ids}] chunks UNCHANGED (preserving the existing structure,
    not inventing a second one) plus a `nodes` lookup so the frontend can
    hydrate each citation's name/file/lines/source without the backend
    reshaping what QAAgent already returns."""
    try:
        local_path = scanner.clone(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db_path = _db_path_for(Path(local_path).name)
    if not db_path.exists():
        raise HTTPException(status_code=404,
                             detail="Repository not indexed yet. Index it first.")

    store = SqliteGraphStore(str(db_path))
    if store.get_repo_by_url(req.url) is None:
        raise HTTPException(status_code=404,
                             detail="Repository not indexed yet. Index it first.")

    try:
        llm = _get_llm()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        chunks = QAAgent(store, llm).answer(req.question)
    except Exception as exc:  # Groq rate limits, transient API errors, etc.
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    node_ids = sorted({node_id for chunk in chunks for node_id in chunk["node_ids"]})
    nodes = {}
    for node_id in node_ids:
        node = store.get_node(node_id)
        if node is not None:
            nodes[str(node_id)] = {
                "id": node.id, "type": node.type.value, "name": node.name,
                "file_path": node.file_path, "start_line": node.start_line,
                "end_line": node.end_line, "source_text": node.source_text,
            }

    return {"chunks": chunks, "nodes": nodes}


@app.post("/trace")
def trace(req: TraceRequest):
    """Flow trace over an already-indexed repo. Calls FlowTracer.trace
    (core/agent/flow_tracer.py, step 8) directly and returns its
    {nodes, edges, steps} UNCHANGED -- only adding full source_text onto
    each node dict, since FlowTracer's own node payload is summary-only
    (see _node_payload). Same pattern as /ask attaching source_text to
    citations: augmenting the existing structure, not inventing a second
    one."""
    try:
        local_path = scanner.clone(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db_path = _db_path_for(Path(local_path).name)
    if not db_path.exists():
        raise HTTPException(status_code=404,
                             detail="Repository not indexed yet. Index it first.")

    store = SqliteGraphStore(str(db_path))
    if store.get_repo_by_url(req.url) is None:
        raise HTTPException(status_code=404,
                             detail="Repository not indexed yet. Index it first.")

    try:
        llm = _get_llm()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        result = FlowTracer(store, llm).trace(req.query, req.target)
    except Exception as exc:  # Groq rate limits, transient API errors, etc.
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    for node in result["nodes"]:
        full = store.get_node(node["id"])
        if full is not None:
            node["source_text"] = full.source_text

    return result


# ---------------- internals ----------------

def _db_path_for(repo_name: str) -> Path:
    return _DB_ROOT / repo_name / "graph.db"


def _get_llm():
    global _llm_provider
    if _llm_provider is None:
        from adapters.llm.groq_provider import GroqProvider
        _llm_provider = GroqProvider()
    return _llm_provider


def _repo_payload(repo) -> dict:
    return {
        "name": repo.name, "source_url": repo.source_url,
        "local_path": repo.local_path, "indexed_at": repo.indexed_at,
    }


def _counts(store: SqliteGraphStore, repo_id: int) -> dict:
    # Direct SQL, not a new GraphStore method: this is a status-display
    # projection for the frontend's feedback banners, not query-contract
    # logic anything else in the system depends on.
    node_count = store._conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE repo_id=? AND file_path != '<external>'",
        (repo_id,),
    ).fetchone()[0]
    edge_count = store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE repo_id=?", (repo_id,),
    ).fetchone()[0]
    return {"node_count": node_count, "edge_count": edge_count}