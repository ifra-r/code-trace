# CodeTrace

CodeTrace turns an unfamiliar GitHub repository into a structured, queryable map of its code and relationships. It builds the understanding once and stores it as a code knowledge graph, so developers and AI agents can reuse the same understanding instead of starting from scratch every time.

## What it does

Point CodeTrace at a public GitHub repo. It clones the repo, parses every Python file with the standard library `ast` module, resolves function and method calls into a graph, and stores the result in SQLite. No LLM calls happen during this step. It is pure, deterministic static analysis.

Once indexed, you can:

- **Ask questions** about the codebase in plain English. Answers are built from real evidence in the graph, not free-form generation, and every claim is tied to an exact file and line range. Answers that cannot be backed by real source evidence are dropped before you ever see them.
- **Trace call flows** through the codebase, visualized as an interactive graph, with a plain-English narration of each step tied to the exact node it describes.
- **Connect external AI agents** (such as Claude Code) to the same graph over MCP, so they get the identical grounded understanding CodeTrace's own UI uses, not a separate reimplementation.

## Why it is built this way

Understanding a new codebase by hand is slow: searching files, following calls, and rebuilding a mental model from scratch every time. CodeTrace builds that model once, persists it, and lets both humans and AI agents query it repeatedly.

It is also token efficient by design. The expensive, repeatable part, parsing and mapping the codebase, costs zero LLM tokens. AI is used only where it adds real value: turning a natural language question into a grounded answer, and narrating a traced call path. Every answer is checked against the graph before being shown, so the system cannot hallucinate a citation that does not exist.

## Architecture

CodeTrace follows a ports and adapters (hexagonal) design. Core domain logic depends only on small interfaces, never on concrete libraries.

```
GitHub URL
  -> clone (adapters/vcs)
  -> parse into symbols (adapters/parsing, stdlib ast)
  -> resolve calls, persist nodes and edges (core/graph, adapters/storage)
  -> BM25 + exact-name search index (core/retrieval)
  -> Q&A agent and flow tracer (core/agent) query the graph
  -> FastAPI (api/) and MCP server (mcp_server/) expose the same graph
     as two independent adapters, both backed by one SQLite file per repo
```

Every citation anywhere in the system is a node id. Resolving a node id always yields a file path and exact start and end lines, which is what makes every answer clickable and verifiable.

## Tech stack

- **Backend:** FastAPI, Python `ast` for parsing, SQLite for storage, `rank_bm25` for retrieval, Groq (`openai/gpt-oss-120b`) for the LLM query layer
- **Frontend:** React, Vite, `react-router-dom`, `react-flow` for graph visualization
- **Integration:** MCP server exposing the graph as tools for external AI agents

## Project structure

```
core/            domain logic: graph model, traversal, retrieval, agents
adapters/        concrete implementations: parsing, storage, LLM, VCS
api/             FastAPI app, thin wiring only
mcp_server/      MCP server exposing the graph as tools
frontend/        React app: landing page, Q&A chat, flow trace visualization
.codegraph/      cloned repos and per-repo SQLite databases (generated)
```

## Getting started

### Backend

```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here   # free tier: https://console.groq.com
uvicorn api.app:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the frontend URL, paste a public GitHub repo URL, and index it. Once indexing finishes you can jump straight to Q&A or Flow Trace.

### MCP server (optional)

```bash
CODEGRAPH_DB_PATH=.codegraph/dbs/<repo_name>/graph.db python -m mcp_server.server
```

Connect any MCP-compatible client (such as Claude Code) to query the same graph directly: `search_symbol`, `get_node`, `get_callers`, `get_callees`, `get_source`, and `trace_path`.

## Known limitations

- Python only, single default branch, no incremental re-indexing.
- Repo identity is keyed by the exact URL string used to index it, so two different URL formats for the same repo are treated as separate entries.
- The MCP server is built to serve one repo's database at a time.
 