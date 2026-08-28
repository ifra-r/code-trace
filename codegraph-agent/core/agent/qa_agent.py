"""Q&A agent: search -> neighbor expansion -> structured LLM chunks ->
evidence validation. Implemented in step 7.

Flow (design doc §9):
  search_symbol(question) -> candidate nodes
    -> expand via get_callers/get_callees for context
    -> prompt LLM to return structured JSON (not free text): a list of
       {text, node_ids} chunks
    -> validate every node_id against GraphStore.get_node (core/agent/evidence.py)
    -> drop any chunk whose ids don't resolve or whose node_ids is empty
    -> surviving chunks ARE the answer

No LLM call happens at all if search_symbols finds nothing relevant --
see _gather_context. That's not an optimization, it's the grounding
guarantee: an LLM given zero evidence blocks would have nothing truthful to
cite, so we never ask it to.

Retrieval note: bm25_retriever.py's exact/prefix/qualified-name match boost
only fires when the QUERY ITSELF looks like a symbol name (e.g. "Flask",
"Session.request") -- a full natural-language sentence like "What does the
Session class represent?" never triggers it, so raw BM25 term overlap alone
can miss the actual symbol entirely (noise words coincidentally matching
rare tokens elsewhere in the repo outscore the real, but lexically common,
symbol name). _extract_symbol_candidates pulls identifier-looking tokens out
of the question and searches each on its own first, so the exact-name boost
gets a chance to fire, before falling back to the full-question BM25 search.
"""

import json
import re
from typing import Dict, List, Tuple

from core.agent.evidence import validate_chunks
from core.graph.models import EdgeType, Node
from core.retrieval.interfaces import GraphStore, LLMProvider

_MAX_CANDIDATES = 6          # top search_symbols hits used as seeds (full-question fallback)
_MAX_CONTEXT_NODES = 20      # cap total nodes (candidates + expansion) sent to the LLM
_MAX_SOURCE_CHARS = 1200     # per-node source_text truncation in the prompt
_MAX_EXTRACTED_TOKENS = 6    # cap identifier-like tokens pulled from the question
_PER_TOKEN_SEARCH_LIMIT = 3  # hits kept per extracted-token search

_IDENTIFIER_LIKE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# Common question words that would otherwise look "code-like" (capitalized
# because they're sentence-initial, etc.) and pollute the targeted searches.
_QUESTION_STOPWORDS = {
    "what", "does", "do", "did", "is", "are", "the", "a", "an", "of", "to",
    "and", "or", "how", "why", "when", "which", "who", "where",
    "method", "class", "function", "represent", "represents", "call",
    "calls", "called", "actually", "purpose", "for", "in", "on", "with",
    "that", "this", "it", "its", "work", "works", "used", "use", "about",
    "explain", "describe", "tell", "me", "you", "your", "please",
}


def _extract_symbol_candidates(question: str) -> List[str]:
    """Best-effort extraction of code-identifier-looking tokens: dotted
    paths (Session.request), PascalCase/camelCase (HTTPAdapter), snake_case,
    or a plain Capitalized word -- while skipping common English question
    words so sentence-initial capitalization ("What does...") doesn't get
    treated as a symbol name."""
    found: List[str] = []
    for match in _IDENTIFIER_LIKE_RE.finditer(question):
        if len(found) >= _MAX_EXTRACTED_TOKENS:
            break
        token = match.group(0)
        if len(token) < 3 or token.lower() in _QUESTION_STOPWORDS:
            continue
        looks_like_code = (
            "." in token
            or "_" in token
            or any(c.isupper() for c in token[1:])  # camelCase/PascalCase
            or token[:1].isupper()                   # plain Capitalized word
        )
        if looks_like_code and token not in found:
            found.append(token)
    return found


class QAAgent:
    def __init__(self, store: GraphStore, llm: LLMProvider) -> None:
        self.store = store
        self.llm = llm

    def answer(self, question: str) -> List[dict]:
        """Returns validated [{text, node_ids}] chunks with resolved citations."""
        context_nodes, relations = self._gather_context(question)
        if not context_nodes:
            return []

        prompt = self._build_prompt(question, context_nodes, relations)
        raw = self.llm.complete(prompt)
        chunks = self._parse_chunks(raw)
        return validate_chunks(chunks, self.store)

    # ---------------- context gathering ----------------

    def _gather_context(self, question: str) -> Tuple[List[Node], Dict[int, str]]:
        """Returns (nodes, relations) where relations[node_id] is a short
        human-readable reason that node is in context (which search matched
        it, or which CALLS edge pulled it in) -- fed into the prompt so the
        LLM sees graph relationships stated explicitly rather than having to
        infer them from source text alone."""
        context: List[Node] = []
        relations: Dict[int, str] = {}

        def add(node: Node, relation: str) -> None:
            if node.id in relations or len(context) >= _MAX_CONTEXT_NODES:
                return
            context.append(node)
            relations[node.id] = relation

        # 1) targeted searches on identifier-like tokens pulled from the
        #    question -- gives the exact/prefix/qualified-name boost in
        #    bm25_retriever.py a real chance to fire.
        for token in _extract_symbol_candidates(question):
            for node in self.store.search_symbols(token, limit=_PER_TOKEN_SEARCH_LIMIT):
                add(node, f"matched search term {token!r}")

        # 2) full-question BM25 as a fallback/supplement, e.g. for questions
        #    with no obvious identifier tokens at all.
        for node in self.store.search_symbols(question, limit=_MAX_CANDIDATES):
            add(node, "matched the question's overall text")

        # 3) neighbor expansion, only around the seeds found above
        seed_nodes = list(context)
        for node in seed_nodes:
            if len(context) >= _MAX_CONTEXT_NODES:
                break
            for callee in self.store.get_neighbors(node.id, EdgeType.CALLS, direction="out"):
                add(callee, f"called BY {node.name} (node_id={node.id})")
            for caller in self.store.get_neighbors(node.id, EdgeType.CALLS, direction="in"):
                add(caller, f"CALLS {node.name} (node_id={node.id})")

        return context, relations

    # ---------------- prompt construction ----------------

    def _build_prompt(self, question: str, nodes: List[Node],
                       relations: Dict[int, str]) -> str:
        blocks = []
        for node in nodes:
            source = node.source_text[:_MAX_SOURCE_CHARS]
            if len(node.source_text) > _MAX_SOURCE_CHARS:
                source += "\n... (truncated)"
            blocks.append(
                f"[node_id={node.id}] {node.type.value} {node.name} "
                f"({node.file_path}:{node.start_line}-{node.end_line})\n"
                f"relation: {relations.get(node.id, 'seed match')}\n"
                f"docstring: {node.docstring or '(none)'}\n"
                f"source:\n{source}"
            )
        evidence_block = "\n\n".join(blocks)

        return f"""You are a code-understanding assistant. Answer the QUESTION
using ONLY the EVIDENCE below -- real source code from the repository, each
block tagged with its exact node_id and, where relevant, its graph relation
to another block (e.g. "called BY Session.request (node_id=246)" means this
block's function IS the one Session.request calls). Do not use outside
knowledge. Do not invent node ids.

Respond with ONLY a JSON array, no prose, no markdown code fences, no keys
other than these two per item:
[{{"text": "one claim or explanation, in your own words", "node_ids": [<int>, ...]}}, ...]

Rules:
- Every array item's "node_ids" must be a non-empty list of node_id integers
  taken verbatim from the EVIDENCE blocks below that directly support that
  item's "text".
- Split the answer into multiple short items rather than one long item, so
  each item can be tied to the specific node(s) that support it.
- Use the "relation" lines to state relationships explicitly when the
  question asks about them (e.g. what a method calls) -- if EVIDENCE shows
  "relation: called BY Session.request (node_id=246)" on a block, that block
  IS the answer to "what does Session.request call".
- If the EVIDENCE does not contain enough information to answer, respond
  with exactly: []
- Never fabricate a node_id that is not shown in EVIDENCE.

QUESTION:
{question}

EVIDENCE:
{evidence_block}
"""

    # ---------------- LLM output parsing ----------------

    @staticmethod
    def _parse_chunks(raw: str) -> List[dict]:
        """Best-effort recovery of a JSON array from the raw completion --
        models occasionally wrap output in ```json fences or an outer
        object despite instructions. Anything that still doesn't parse
        cleanly becomes [] (validate_chunks would drop it all anyway;
        failing here just skips the wasted validation pass)."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        if not text:
            return []

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []

        if isinstance(parsed, dict):
            # tolerate a model wrapping the array, e.g. {"chunks": [...]}
            for value in parsed.values():
                if isinstance(value, list):
                    parsed = value
                    break
            else:
                return []

        if not isinstance(parsed, list):
            return []
        return [c for c in parsed if isinstance(c, dict)]