"""Flow tracer: entry point lookup -> bounded call-path DFS -> per-step LLM
narration tied to nodes. Implemented in step 8."""

from core.retrieval.interfaces import GraphStore, LLMProvider


class FlowTracer:
    def __init__(self, store: GraphStore, llm: LLMProvider) -> None:
        self.store = store
        self.llm = llm

    def trace(self, query: str, target: str | None = None) -> dict:
        """Returns ordered path (nodes + edges) for react-flow, with narration."""
        raise NotImplementedError("implemented in build step 8")