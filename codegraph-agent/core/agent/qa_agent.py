"""Q&A agent: search -> neighbor expansion -> structured LLM chunks ->
evidence validation. Implemented in step 7."""

from core.retrieval.interfaces import GraphStore, LLMProvider
from typing import List

class QAAgent:
    def __init__(self, store: GraphStore, llm: LLMProvider) -> None:
        self.store = store
        self.llm = llm

    def answer(self, question: str) -> List[dict]:
        """Returns validated [{text, node_ids}] chunks with resolved citations."""
        raise NotImplementedError("implemented in build step 7")