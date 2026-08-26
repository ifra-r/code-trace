"""rank_bm25 index over node name + docstring + source_text. Implemented in step 5."""

from typing import List

from core.graph.models import Node


class BM25Index:
    def build(self, nodes: list[Node]) -> None:
        raise NotImplementedError("implemented in build step 5")

    def search(self, query: str, limit: int = 10) -> List[Node]:
        raise NotImplementedError("implemented in build step 5")