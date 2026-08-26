# CodeGraph Agent — Hackathon Architecture & Build Plan

Locked plan. Build in this order. Do not change interfaces once a layer is done — that's the entire point of the layering.

## 1. What this is (per submitted description)

> An AI-powered codebase understanding agent that helps developers quickly understand unfamiliar GitHub repositories by analyzing code structure, dependencies, and relationships, then answering questions and tracing code flows using grounded source-code evidence.

Demo has two layers, same underlying system:
1. **Headline**: our own UI — ask a question → Q&A / flow trace → grounded evidence, clickable to exact source lines.
2. **Bonus reveal**: the same graph is exposed via MCP tools. Show Claude Code (or any MCP client) answering the same question using our tools directly — proof this isn't a closed agent, it's infrastructure other agents can use.

Both layers call the *same* core `GraphStore` and agent functions — independently. The chat endpoint does not route through the MCP server, and the MCP server does not route through the chat endpoint:

```
Agent (qa_agent.py, flow_tracer.py) → core GraphStore
MCP tools                           → core GraphStore
```

Two separate thin adapters over one capability. This avoids coupling your own app's request path to the MCP server's lifecycle while still proving external agents get the identical underlying graph.

## 2. Non-negotiable architecture principles

- **Hexagonal / ports and adapters.** Core domain logic has zero dependency on any concrete library. It only knows small interfaces (ports). Concrete parsers, stores, LLM providers are adapters injected in.
- **Graph construction is 100% deterministic, zero LLM calls.** AST parsing, symbol resolution, call graph — pure Python `ast` module. This is the token-efficiency answer: the expensive, repeatable part costs nothing per use.
- **LLM only touches the query layer** (Q&A answer synthesis, flow-trace narration) — and even there, the model returns structured `{text, node_ids[]}` chunks, not free prose; any chunk with unresolvable or missing node ids is dropped before being shown. See §9.
- **One frozen schema, agreed before any pipeline or agent code is written.** Both people build against it in parallel.

## 3. Locked tech stack (hackathon)

| Layer | Decision | Why |
|---|---|---|
| Target repo language | Python only | Most mature stdlib AST tooling for a ~1 week build |
| Build language | Python (backend), TS/React (frontend) | One venv for the whole pipeline |
| Static analysis | `ast` module (stdlib) | Zero extra dependency, no tree-sitter risk this week |
| Graph + structured storage | **SQLite**, single file at `.codegraph/graph.db` per indexed repo | Portable, no service to run, matches "central map lives in/near the repo" goal, no Docker needed |
| Retrieval (no embeddings) | Exact/fuzzy symbol + docstring match, plus `rank_bm25` over chunk text | Deterministic, zero API cost, no vector store dependency |
| Graph traversal | `networkx`, loaded from SQLite into memory per request | Simple, well-tested |
| LLM | Anthropic API, single `LLMProvider` adapter | Only touched at query time, never at index time |
| Backend framework | FastAPI | Fast to write, auto validation |
| MCP server | Python MCP SDK, in-process, calling the same core functions FastAPI calls | This is the bonus-reveal layer |
| Frontend | React + Vite | Needs a real interactive surface for clickable evidence |
| Graph/flow visualization | `react-flow` | Renders knowledge graph + highlighted trace path |
| Orchestration | None needed — `pip install` + `npm install`, run two processes | SQLite removes the reason to use docker-compose this week |

**Deferred to post-hackathon, do not build now:** multi-language support, impact analysis, security-aware data-flow tracing, incremental re-indexing, multi-repo/multi-tenant, CI bot, embeddings/vector search, branch/diff handling (main/master only for now).

## 4. Data model (conceptual contract frozen; physical schema may take small additive changes)

```
repos:  id, name, source_url, local_path, indexed_at
nodes:  id, repo_id, type (file|function|class|method),
        name, file_path, start_line, end_line, source_text, docstring
edges:  id, repo_id, source_node_id, target_node_id,
        type (calls|imports|inherits|contains), resolved (bool)
```

Every citation the agent or an MCP tool ever returns is a **node id**. Resolving a node id always yields `file_path`, `start_line`, `end_line` — this is what makes evidence clickable and is the contract every layer above the graph depends on. Unresolved calls are kept as edges with `resolved=false`, never silently dropped.

**What's actually frozen**: node ids are stable and resolvable to file/line; edges have a `type`; unresolved calls are kept, not dropped. **What's allowed to flex**: adding a column (e.g. a `decorator` field once you see real decorator-heavy code), splitting `type` into finer categories, adding an index. If step 3 (parsing real repos) surfaces a case the schema didn't anticipate, fix the schema then — don't design around every hypothetical now, and don't refuse a small necessary change later just because it says "frozen."

## 5. Core interfaces (ports)

```
Parser.parse(file_path) -> list[Symbol]
GraphStore.get_node(id) -> Node
GraphStore.get_neighbors(id, edge_type) -> list[Node]
GraphStore.get_subgraph(node_ids) -> Graph
GraphStore.search_symbols(query) -> list[Node]   # BM25 + name match
LLMProvider.complete(prompt) -> str
RepoSource.clone(url) -> local_path
```

Everything above the pipeline (agent, MCP tools, API) calls only `GraphStore` and `LLMProvider`. Never the DB or the parser directly.

## 6. Directory layout

```
/core
  /graph        models.py, graph_builder.py, traversal.py
  /retrieval    interfaces.py, bm25_retriever.py
  /agent        qa_agent.py, flow_tracer.py, evidence.py
/adapters
  /parsing      python_parser.py
  /storage      sqlite_store.py
  /llm          anthropic_provider.py
  /vcs          github_scanner.py
/api            FastAPI app — thin, wires adapters into core
/mcp            MCP server — thin, calls the same core functions as /api
/frontend       React + Vite + react-flow
```

`/api` and `/mcp` are two thin adapters over the same core. Neither contains logic — if you're writing logic in either, it belongs in `/core/agent` instead.

## 7. MCP tool surface (the bonus-reveal layer)

| Tool | Input | Returns |
|---|---|---|
| `search_symbol` | query string | matching nodes with file/line |
| `get_node` | node id | full node detail + source snippet |
| `get_callers` | node id | nodes that call this node |
| `get_callees` | node id | nodes this node calls |
| `trace_path` | start node id, end node id or query | ordered path of nodes + edges |
| `get_source` | node id | exact source snippet, line-highlighted |

These map 1:1 to `GraphStore` methods. The MCP server calls `GraphStore` directly, same as the agent does — it does not go through `qa_agent.py`/`flow_tracer.py`, and they don't go through it. No reimplementation, no coupling either direction.

## 8. Pipeline flow

```
GitHub URL
  → RepoSource.clone
  → Parser.parse (per file) → Symbols
  → call resolution (same file, cross-file, best-effort aliased imports)
  → write nodes + edges to SQLite
  → build BM25 index over node source + docstring
  → ready for query (Q&A / trace / MCP tools)
```

No LLM call anywhere in this flow.

## 9. Query layer

**Q&A**: `search_symbol(question)` → candidate nodes → expand via `get_callers`/`get_callees` for context → prompt Claude to return **structured JSON**, not free text: a list of `{text: string, node_ids: [id, ...]}` chunks. Do not attempt to parse claims out of prose — that's unreliable. Validate every `node_id` against `GraphStore.get_node`; drop any chunk whose ids don't resolve, or whose `node_ids` list is empty. Concatenate the surviving chunks as the answer, each with its resolved file/line citations attached.

**Flow trace**: `search_symbol(query)` to find entry point → bounded DFS along `calls` edges via `GraphStore` → optional one-sentence-per-step narration from Claude, each sentence tied to its node → return ordered path for `react-flow`.

## 10. Build order (layers, not days)

1. Frozen schema + core interfaces (empty stubs), both people together.
2. `RepoSource` (clone) + FastAPI/React skeletons talking to each other — smoke test only.
3. `Parser` — walk repo, extract symbols with accurate line numbers. Test against a small and a messy real repo before moving on.
4. Call resolution → nodes + edges written to SQLite. Verify manually against known calls in both test repos.
5. BM25 index over node text. Verify with hand-written test queries.
6. `GraphStore` interface finalized (`get_node`, `get_neighbors`, `get_subgraph`, `search_symbols`) — everything above this line only touches this interface from here on.
7. Q&A agent on top of `GraphStore` + `LLMProvider`. Verify grounded citations on 5+ hand-written questions.
8. Flow tracer on top of the same `GraphStore`. Verify against a manually-traced path.
9. MCP server — thin wrapper exposing step 6's `GraphStore` methods directly as tools (not steps 7/8's agent code). Should take hours, not days, since no new logic is written here.
10. Evidence UI — chat + citation panel, calling the real API, not mocks.
11. Flow trace visualization in `react-flow`.
12. Integration: pre-index both demo repos, confirm SQLite files persist, run full test pass.
13. Rehearsal + recorded backup. No new features past this point.

## 11. Never-cut core (per submitted description)

Static analysis, the graph, Q&A with grounded evidence, flow tracing. If time runs out, cut in this order: MCP polish → flow-trace visualization polish → flow tracing narration quality → nothing else, these four are the product.

## 12. Open items (not architectural — resolve before demo, don't block build start)

- Demo repos (decided): **small/clean = `psf/requests`** (MIT, ~10–15k LOC, clean structure — sessions/adapters/models/auth); **larger/messier = `pallets/flask`** (BSD — decorators, blueprints, context-local proxies, inheritance chains, real AST edge cases worth finding before demo day). Both pure Python, no C extensions, both widely recognized so a flow-trace demo needs no codebase explanation.
- Script the flow-trace query for rehearsal; leave Q&A open to the audience with 2–3 fallback questions ready.
