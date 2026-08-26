"""AST-based Python parser — implements the Parser port (build step 3).

Deterministic, zero LLM, stdlib `ast` only. parse(file_path) returns the FILE
symbol first, followed by every function/class/method symbol in the file —
so graph_builder (step 4) always has the file node available for CONTAINS /
IMPORTS wiring.

Conventions (mechanically pinned by check_step3.py):
- start_line points at the `def`/`async def`/`class` keyword line;
  decorators above it are NOT included in the span.
- end_line is the last line of the definition, inclusive.
- source_text == "\\n".join(original_lines[start_line-1 : end_line]) exactly.
- Functions directly inside a class body are METHOD; all others FUNCTION
  (including nested defs and async).
- parent_name = immediate enclosing def/class name (None at module level).
- Unreadable file -> ValueError. Unparseable file (syntax error) -> []
  with a stderr note — a bad file never crashes the indexing pipeline.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterator

from core.graph.models import NodeType, Symbol
from core.retrieval.interfaces import Parser

_SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", ".tox", ".eggs",
    "node_modules", "build", "dist", ".mypy_cache", ".pytest_cache",
}
_MAX_SOURCE_BYTES = 1_000_000  # guard against pathological files in repo walks


class PythonParser:
    """Parses one Python file into Symbols via the stdlib ast module."""

    def parse(self, file_path: str) -> list[Symbol]:
        text = self._read(file_path)
        lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
            print(f"[python_parser] skipping unparseable {file_path}: {exc}",
                  file=sys.stderr)
            return []

        symbols = [Symbol(
            type=NodeType.FILE,
            name=Path(file_path).name,
            file_path=file_path,
            start_line=1,
            end_line=max(1, len(lines)),
            source_text="\n".join(lines),
            docstring=(ast.get_docstring(tree) or ""),
            parent_name=None,
        )]
        self._walk(tree.body, lines, file_path,
                   parent_name=None, in_class=False, out=symbols)
        return symbols

    def walk_repo(
        self, repo_root: str | Path
    ) -> Iterator[tuple[str, list[Symbol]]]:
        """Yield (repo_relative_path, symbols) for every parseable .py file.
        Skips VCS/build/vendor/hidden dirs. Never raises on individual files."""
        root = Path(repo_root).resolve()
        for abs_path in sorted(root.rglob("*.py")):
            if abs_path.name.startswith("."):
                continue
            dir_parts = abs_path.relative_to(root).parts[:-1]
            if any(p in _SKIP_DIR_NAMES or p.startswith(".")
                   or p.endswith(".egg-info") for p in dir_parts):
                continue
            rel = abs_path.relative_to(root).as_posix()
            try:
                symbols = self.parse(str(abs_path))
            except ValueError as exc:
                print(f"[python_parser] skipping unreadable {rel}: {exc}",
                      file=sys.stderr)
                continue
            for sym in symbols:  # normalize to repo-relative paths
                sym.file_path = rel
            yield rel, symbols

    # ---------------- internals ----------------

    @staticmethod
    def _read(file_path: str) -> str:
        raw = Path(file_path).read_bytes()
        if len(raw) > _MAX_SOURCE_BYTES:
            raise ValueError(f"file exceeds {_MAX_SOURCE_BYTES} bytes")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")  # lossless byte<->char, line math stays exact

    def _walk(self, stmts, lines, file_path, *,
              parent_name, in_class: bool, out: list) -> None:
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(self._symbol(
                    node, NodeType.METHOD if in_class else NodeType.FUNCTION,
                    lines, file_path, parent_name))
                self._walk(node.body, lines, file_path,
                           parent_name=node.name, in_class=False, out=out)
            elif isinstance(node, ast.ClassDef):
                out.append(self._symbol(
                    node, NodeType.CLASS, lines, file_path, parent_name))
                self._walk(node.body, lines, file_path,
                           parent_name=node.name, in_class=True, out=out)

    @staticmethod
    def _symbol(node, sym_type: NodeType, lines, file_path, parent_name) -> Symbol:
        start = node.lineno                    # def/class keyword line
        end = node.end_lineno or start         # end_lineno exists on 3.8+
        return Symbol(
            type=sym_type,
            name=node.name,
            file_path=file_path,
            start_line=start,
            end_line=end,
            source_text="\n".join(lines[start - 1:end]),
            docstring=(ast.get_docstring(node) or ""),
            parent_name=parent_name,
        )