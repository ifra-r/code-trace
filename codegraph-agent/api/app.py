"""FastAPI layer — thin wiring only (design doc §6).
If you're writing logic here, it belongs in /core instead.
Step 2: /health + /clone (real) + /index (stub until steps 3-5)."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from adapters.vcs.github_scanner import GitHubScanner

app = FastAPI(title="CodeGraph Agent")

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


class RepoRequest(BaseModel):
    url: str


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
def index_repo(req: RepoRequest):
    """Interface locked now; real pipeline (parse → resolve → SQLite → BM25)
    lands in steps 3-5."""
    raise HTTPException(status_code=501, detail="indexing implemented in build steps 3-5")