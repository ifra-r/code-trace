"""BM25 symbol/text retriever (build step 5, extended in step 6). Deterministic,
no LLM, no embeddings -- the design doc's retrieval choice (§3): "Exact/fuzzy
symbol + docstring match, plus rank_bm25 over chunk text."

Implemented as ONE ranked list rather than two separate mechanisms: BM25
scores `qualified_name + name + docstring + source_text`, and exact/prefix/
substring matches on the symbol name (or its dotted qualified name, e.g.
"Session.request") are added as a score boost on top. Without the qualified
name, a query for "Session.request" scored no better than any other function
named "request" repo-wide -- the boost needs the class-qualified string to
disambiguate, since BM25 alone can't tell "Session.request" apart from
"OtherClass.request" by relevance score.

Library-free at the token level: identifiers are split on snake_case AND
camelCase/PascalCase boundaries (HTTPAdapter -> ["http", "adapter"]) so code
identifiers behave like natural-language tokens for matching purposes.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from rank_bm25 import BM25Okapi

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

# Score boosts applied on top of the raw BM25 score, in descending strength.
_EXACT_NAME_BOOST = 1000.0
_PREFIX_NAME_BOOST = 100.0
_SUBSTRING_NAME_BOOST = 10.0


def tokenize(text: str) -> List[str]:
    """Extracts identifiers, splits each on snake_case/camelCase boundaries,
    lowercases. `get_neighbors` and `HTTPAdapter.send` both tokenize the way
    a human reading them out loud would."""
    tokens: List[str] = []
    for raw in _IDENTIFIER_RE.findall(text):
        tokens.extend(_split_identifier(raw))
    return [t.lower() for t in tokens if t]


def _split_identifier(raw: str) -> List[str]:
    parts = [p for p in raw.split("_") if p]
    if not parts:
        return [raw]
    out: List[str] = []
    for part in parts:
        sub = _CAMEL_RE.findall(part)
        out.extend(sub if sub else [part])
    return out


class BM25Retriever:
    """In-memory BM25 index over a fixed corpus of (node_id, name, text)
    triples. Built once per corpus snapshot, queried many times. Owns no
    SQLite/Node knowledge -- the caller decides what goes into `text`
    (the store puts the qualified name first, e.g. "Session.request ...")."""

    def __init__(self, docs: Sequence[Tuple[int, str, str]]) -> None:
        self._doc_ids: List[int] = [d[0] for d in docs]
        self._names: List[str] = [d[1] for d in docs]
        self._texts: List[str] = [d[2] for d in docs]
        tokenized = [tokenize(d[2]) for d in docs]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def search(self, query: str, limit: int = 10) -> List[Tuple[int, float]]:
        """Returns (node_id, score) pairs, highest score first, score > 0
        only -- queries with no lexical overlap correctly return nothing
        rather than an arbitrary low-score ordering."""
        if self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)
        query_lower = query.strip().lower()

        boosted: List[Tuple[int, float]] = []
        for idx, score in enumerate(scores):
            name_lower = self._names[idx].lower()
            text_lower = self._texts[idx].lower()
            # text is "qualified_name name docstring source_text" -- a query
            # that matches the qualified name exactly (e.g. "session.request")
            # should boost just as hard as an exact bare-name match.
            qualified_exact = bool(query_lower) and text_lower.startswith(
                query_lower + " ")

            if query_lower and (name_lower == query_lower or qualified_exact):
                score += _EXACT_NAME_BOOST
            elif query_lower and name_lower.startswith(query_lower):
                score += _PREFIX_NAME_BOOST
            elif query_lower and query_lower in name_lower:
                score += _SUBSTRING_NAME_BOOST
            boosted.append((self._doc_ids[idx], float(score)))

        boosted.sort(key=lambda pair: pair[1], reverse=True)
        return [(nid, sc) for nid, sc in boosted[:limit] if sc > 0]