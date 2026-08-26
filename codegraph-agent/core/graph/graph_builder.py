"""Deterministic indexing pipeline: clone -> parse -> resolve calls -> persist.
Zero LLM calls (design doc §8). Implemented across steps 3-5."""

from core.retrieval.interfaces import GraphStore, Parser, RepoSource


class GraphBuilder:
    def __init__(self, source: RepoSource, parser: Parser, store: GraphStore) -> None:
        self.source = source
        self.parser = parser
        self.store = store

    def index_repo(self, url: str) -> int:
        """Returns the repo id once nodes+edges are written."""
        raise NotImplementedError("implemented in build steps 3-5")