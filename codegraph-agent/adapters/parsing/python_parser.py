"""AST-based Python parser — implements the Parser port. Implemented in step 3."""

from core.graph.models import Symbol
from core.retrieval.interfaces import Parser


class PythonParser:
    def parse(self, file_path: str) -> list[Symbol]:
        raise NotImplementedError("implemented in build step 3")