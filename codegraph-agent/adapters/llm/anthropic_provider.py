"""Anthropic adapter — implements the LLMProvider port. Implemented in step 7."""

import os

from core.retrieval.interfaces import LLMProvider


class AnthropicProvider:
    def __init__(self, model: str | None = None) -> None:
        # override via ANTHROPIC_MODEL env var if desired
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    def complete(self, prompt: str) -> str:
        raise NotImplementedError("implemented in build step 7")