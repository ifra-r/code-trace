"""Groq adapter — implements the LLMProvider port. Implemented in step 7."""

import os
from typing import Optional

from groq import Groq

from core.retrieval.interfaces import LLMProvider

_DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqProvider:
    """Single-shot completion adapter over Groq's chat API. Query-time only
    (design doc §2/§8) -- never called while indexing.

    Deliberately a plain complete(prompt) -> str adapter with no forced
    response_format: this same port is used for the Q&A agent's JSON-only
    chunks (step 7) AND flow-trace free-text narration (step 8). Any
    structured-output contract lives in the caller's prompt, not here.
    """

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        # override via GROQ_MODEL env var if desired
        self.model = model or os.getenv("GROQ_MODEL", _DEFAULT_MODEL)
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY not set. Get a free key at https://console.groq.com "
                "and `export GROQ_API_KEY=...` (or pass api_key= explicitly)."
            )
        self._client = Groq(api_key=key)

    def complete(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return response.choices[0].message.content or ""