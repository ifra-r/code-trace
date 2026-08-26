"""Grounding guard (design doc §9): validate LLM chunks' node_ids against the
store; drop chunks whose ids don't resolve or whose node_ids list is empty.
Implemented in step 7."""

from typing import List

from core.retrieval.interfaces import GraphStore


def validate_chunks(chunks: List[dict], store: GraphStore) -> List[dict]:
    raise NotImplementedError("implemented in build step 7")