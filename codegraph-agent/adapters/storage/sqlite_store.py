"""SQLite-backed GraphStore — one DB file per indexed repo: .codegraph/graph.db.

Write side lands in step 4, search in step 5, full query contract in step 6.
"""

from typing import List, Optional, Sequence

from core.graph.models import Edge, EdgeType, Node, Repo, Subgraph
from core.retrieval.interfaces import Direction, GraphStore
from core.retrieval.interfaces import GraphStore


class SqliteGraphStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # ---- frozen query contract ----
    def get_node(self, node_id: int) -> Optional[Node]:
        raise NotImplementedError("implemented in build step 6")

    def get_neighbors(
        self,
        node_id: int,
        edge_type: Optional[EdgeType] = None,
        *,
        direction: Direction = "out",
    ) -> List[Node]:
        raise NotImplementedError("implemented in build step 6")

    def get_subgraph(self, node_ids: Sequence[int]) -> Subgraph:
        raise NotImplementedError("implemented in build step 6")

    def search_symbols(self, query: str, limit: int = 10) -> List[Node]:
        raise NotImplementedError("implemented in build steps 5-6")

    # ---- pipeline-facing write side ----
    def add_repo(self, name: str, source_url: str, local_path: str) -> int:
        raise NotImplementedError("implemented in build step 4")

    def get_repo_by_url(self, source_url: str) -> Optional[Repo]:
        raise NotImplementedError("implemented in build step 4")

    def add_node(self, node: Node) -> int:
        raise NotImplementedError("implemented in build step 4")

    def add_edge(self, edge: Edge) -> int:
        raise NotImplementedError("implemented in build step 4")