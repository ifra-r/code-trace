"""Bounded DFS along resolved `calls` edges, networkx-backed. Implemented in step 8."""

from typing import List, Optional

from core.graph.models import Node
from core.retrieval.interfaces import GraphStore


def find_call_path(
    store: GraphStore,
    start_id: int,
    goal_id: Optional[int] = None,
    max_depth: int = 12,
) -> List[Node]:
    raise NotImplementedError("implemented in build step 8")