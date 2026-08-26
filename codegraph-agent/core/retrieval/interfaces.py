"""
Core ports (hexagonal interfaces, design doc §5).

/api, /core/agent, and /mcp may ONLY depend on these protocols and
core.graph.models — never on concrete adapters, the DB, or the parser.

Frozen query contract on GraphStore: get_node, get_neighbors, get_subgraph,
search_symbols. Write-side methods are pipeline-facing plumbing used only by
graph_builder during indexing (additive changes allowed per §4).
"""

from __future__ import annotations

from typing import List, Literal, Optional, Protocol, Sequence, runtime_checkable

from core.graph.models import Edge, EdgeType, Node, Repo, Subgraph, Symbol

Direction = Literal["in", "out", "both"]


@runtime_checkable
class Parser(Protocol):
    def parse(self, file_path: str) -> List[Symbol]:
        """Parse one source file into raw symbols. Pure AST, deterministic."""
        ...


@runtime_checkable
class RepoSource(Protocol):
    def clone(self, url: str) -> str:
        """Clone/fetch a repository; return its local filesystem path."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    def complete(self, prompt: str) -> str:
        """Single-shot completion. Query-time only — never called while indexing."""
        ...


@runtime_checkable
class GraphStore(Protocol):
    # ---------- frozen query contract (agent / MCP / API consume these) ----------

    def get_node(self, node_id: int) -> Optional[Node]:
        """Resolve a node id to a full Node (incl. file/lines). None if unknown."""
        ...

    def get_neighbors(
        self,
        node_id: int,
        edge_type: Optional[EdgeType] = None,
        *,
        direction: Direction = "out",
    ) -> List[Node]:
        """
        Nodes connected to node_id.
          direction="out"  -> callees   (edges leaving node_id)
          direction="in"   -> callers   (edges entering node_id)
        So: callers(n) == get_neighbors(n, EdgeType.CALLS, direction="in")
        """
        ...

    def get_subgraph(self, node_ids: Sequence[int]) -> Subgraph:
        """Induced subgraph over node_ids (known ids only) + edges among them."""
        ...

    def search_symbols(self, query: str, limit: int = 10) -> List[Node]:
        """BM25 + exact/fuzzy name & docstring match. Deterministic, no LLM."""
        ...

    # ---------- pipeline-facing write side (only graph_builder touches) ----------

    def add_repo(self, name: str, source_url: str, local_path: str) -> int:
        """Insert repo row, return repo id."""
        ...

    def get_repo_by_url(self, source_url: str) -> Optional[Repo]:
        ...

    def add_node(self, node: Node) -> int:
        """Persist node; return its assigned id (use that id when wiring edges)."""
        ...

    def add_edge(self, edge: Edge) -> int:
        """Persist edge; return its assigned id."""
        ...