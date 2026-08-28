"""Grounding guard (design doc §9): validate LLM chunks' node_ids against the
store; drop chunks whose ids don't resolve or whose node_ids list is empty.
Implemented in step 7."""

from typing import Any, List

from core.retrieval.interfaces import GraphStore


def validate_chunks(chunks: List[Any], store: GraphStore) -> List[dict]:
    """Keeps only chunks shaped like {"text": str, "node_ids": [int, ...]}
    where node_ids is non-empty and every single id resolves via
    store.get_node. This is the only place unverified LLM output is allowed
    to become part of an answer -- anything that doesn't pass here is
    dropped whole, never partially kept or "fixed up". A chunk citing three
    ids where one is bogus is dropped entirely, since the surviving two ids
    might not actually support the claim's text on their own."""
    validated: List[dict] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue

        text = chunk.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        node_ids = chunk.get("node_ids")
        if not isinstance(node_ids, list) or len(node_ids) == 0:
            continue

        resolved_ids: List[int] = []
        all_resolve = True
        for raw_id in node_ids:
            if isinstance(raw_id, bool):  # bool is an int subclass -- reject explicitly
                all_resolve = False
                break
            try:
                node_id = int(raw_id)
            except (TypeError, ValueError):
                all_resolve = False
                break
            if store.get_node(node_id) is None:
                all_resolve = False
                break
            resolved_ids.append(node_id)

        if not all_resolve:
            continue

        validated.append({"text": text.strip(), "node_ids": resolved_ids})
    return validated