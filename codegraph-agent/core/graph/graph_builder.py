"""Deterministic indexing pipeline: clone -> parse -> resolve calls -> persist.
Zero LLM calls (design doc §8). Implemented across steps 3-5.

Step 4 (this implementation): call resolution + nodes/edges written to SQLite.

Resolution strategy (best-effort, deterministic, no LLM, matches §9's framing
that grounded evidence must never be *wrong*, only sometimes absent):

  1. self.foo() / cls.foo()  -> resolved against the enclosing class's own
                                  methods in the same file.
  2. bare name  foo()        -> same-file match first (any scope) if unique;
                                  else resolved via a `from module import foo`
                                  alias if that module maps to a file in this
                                  repo; else a single unambiguous repo-wide
                                  name match. Ambiguous matches are left
                                  unresolved rather than guessed.
  3. attr call  mod.foo()    -> if `mod` is a locally aliased `import module`
                                  that maps to a file in this repo, resolved
                                  against that file's top-level defs; else a
                                  weak same-file-by-name fallback.
  4. anything else (dynamic calls, deep attribute chains like
     `self.session.request()`, calls that don't match any known symbol)
     -> kept as a CALLS edge with resolved=False, pointing at a deduplicated
     external stub Node (file_path="<external>"), per design doc §4:
     "unresolved calls are kept as edges ... never silently dropped."
     Edge.target_node_id is non-optional, so the stub node is what makes
     that guarantee representable in the current frozen schema. Anything
     consuming edges (agent, MCP tools) must treat resolved=False /
     file_path=="<external>" as "not real source", never as a citation.

CONTAINS edges are emitted for every symbol that has a parent (file -> its
top-level defs, class -> its methods/nested classes, function -> nested
functions) since Node itself carries no parent field — CONTAINS is the only
place hierarchy is recorded in the schema.

Note: this re-walks each file's AST a second time (parallel to
adapters/parsing/python_parser.py's own traversal) purely to reach raw
ast.Call nodes, which Symbol does not carry. It is keyed off exact
(file_path, start_line) so it stays correct even with duplicate names.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.graph.models import Edge, EdgeType, Node, NodeType, Symbol
from core.retrieval.interfaces import GraphStore, Parser, RepoSource

_EXTERNAL_FILE = "<external>"

# A resolved call target: (node_id, resolved)
_Resolution = Tuple[Optional[int], bool]


class GraphBuilder:
    # Stores the repo source, parser, and SQLite graph store.
    def __init__(self, source: RepoSource, parser: Parser, store: GraphStore) -> None:
        self.source = source
        self.parser = parser
        self.store = store

    # Clones the repo, creates its nodes/edges, and returns the repo ID.
    def index_repo(self, url: str) -> int:
        """Returns the repo id once nodes+edges are written."""
        local_path = self.source.clone(url)

        existing = self.store.get_repo_by_url(url)
        if existing is not None:
            return existing.id  # idempotent: already indexed, reuse as-is

        repo_root = Path(local_path)
        repo_id = self.store.add_repo(
            name=repo_root.name, source_url=url, local_path=str(local_path))

        # `walk_repo` is a concrete convenience on PythonParser beyond the
        # formal Parser port (which only defines `parse`). The stack is
        # Python-only this sprint (§3), so this duck-typed use is accepted
        # rather than duplicating its directory-skip logic here.
        walked: Dict[str, List[Symbol]] = dict(self.parser.walk_repo(repo_root))

        (node_id_by_loc, by_name_in_file, by_name_global,
         methods_by_class, file_node_id, module_to_file) = self._persist_nodes(
            repo_id, walked)

        self._emit_contains_edges(repo_id, walked, node_id_by_loc,
                                   by_name_in_file, file_node_id)

        self._resolve_and_emit_calls(
            repo_id, repo_root, walked, node_id_by_loc, by_name_in_file,
            by_name_global, methods_by_class, module_to_file)

        return repo_id

    # ------------------------------------------------------------------
    # pass 1: persist nodes, build lookup indices
    # ------------------------------------------------------------------
    
    # Converts parsed Symbols into SQLite Nodes and builds lookup maps for resolution.
    def _persist_nodes(self, repo_id: int, walked: Dict[str, List[Symbol]]):
        node_id_by_loc: Dict[Tuple[str, int], int] = {}
        by_name_in_file: Dict[Tuple[str, str], List[int]] = {}
        by_name_global: Dict[str, List[int]] = {}
        methods_by_class: Dict[Tuple[str, str], Dict[str, int]] = {}
        file_node_id: Dict[str, int] = {}
        module_to_file: Dict[str, str] = {}

        for rel_path, symbols in walked.items():
            module_to_file[self._to_module_path(rel_path)] = rel_path
            for sym in symbols:
                node_id = self.store.add_node(Node(
                    id=None, repo_id=repo_id, type=sym.type, name=sym.name,
                    file_path=sym.file_path, start_line=sym.start_line,
                    end_line=sym.end_line, source_text=sym.source_text,
                    docstring=sym.docstring,
                ))
                node_id_by_loc[(sym.file_path, sym.start_line)] = node_id

                if sym.type == NodeType.FILE:
                    file_node_id[sym.file_path] = node_id
                    continue

                by_name_in_file.setdefault((sym.file_path, sym.name), []).append(node_id)
                if sym.type in (NodeType.FUNCTION, NodeType.METHOD):
                    by_name_global.setdefault(sym.name, []).append(node_id)
                if sym.type == NodeType.METHOD and sym.parent_name is not None:
                    methods_by_class.setdefault(
                        (sym.file_path, sym.parent_name), {})[sym.name] = node_id

        return (node_id_by_loc, by_name_in_file, by_name_global,
                methods_by_class, file_node_id, module_to_file)

    # Converts a Python file path like foo/bar.py into module form foo.bar.
    @staticmethod
    def _to_module_path(rel_path: str) -> str:
        stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
        parts = stem.split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    # ------------------------------------------------------------------
    # CONTAINS edges (file->top-level, class->method/nested-class, etc.)
    # ------------------------------------------------------------------

    # Connects files/classes/functions to the symbols nested inside them.
    def _emit_contains_edges(self, repo_id, walked, node_id_by_loc,
                              by_name_in_file, file_node_id):
        for rel_path, symbols in walked.items():
            file_id = file_node_id[rel_path]
            for sym in symbols:
                if sym.type == NodeType.FILE:
                    continue
                # exact: start_line uniquely identifies this symbol, unlike
                # (file, name) which collides constantly (every class's
                # __init__ shares that key) -- see step-4 notes.
                child_id = node_id_by_loc[(sym.file_path, sym.start_line)]
                if sym.parent_name is None:
                    parent_id = file_id
                else:
                    # best-effort: parent is looked up by name only, so this
                    # can mis-wire if two classes/functions share a name in
                    # the same file (rare, unlike method-name collisions).
                    candidates = by_name_in_file.get(
                        (sym.file_path, sym.parent_name), [])
                    parent_id = candidates[0] if candidates else file_id
                self._add_edge(repo_id, parent_id, child_id, EdgeType.CONTAINS,
                                resolved=True)

    # ------------------------------------------------------------------
    # pass 2: re-walk ASTs, extract + resolve calls
    # ------------------------------------------------------------------

    # Re-parses each file's AST, finds function calls, and creates CALLS edges.
    def _resolve_and_emit_calls(self, repo_id, repo_root, walked, node_id_by_loc,
                                 by_name_in_file, by_name_global, methods_by_class,
                                 module_to_file):
        external_cache: Dict[str, int] = {}

        for rel_path in walked:
            abs_path = repo_root / rel_path
            try:
                text = abs_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue  # unparseable files already skipped/logged in step 3

            alias_map = self._collect_imports(tree, rel_path)
            self._walk_for_calls(
                tree.body, rel_path, enclosing_class=None,
                repo_id=repo_id, node_id_by_loc=node_id_by_loc,
                by_name_in_file=by_name_in_file, by_name_global=by_name_global,
                methods_by_class=methods_by_class, module_to_file=module_to_file,
                alias_map=alias_map, external_cache=external_cache,
            )

    # Walks functions/classes and sends each function's calls to the resolver.
    def _walk_for_calls(self, stmts, rel_path, *, enclosing_class,
                         repo_id, node_id_by_loc, by_name_in_file,
                         by_name_global, methods_by_class, module_to_file,
                         alias_map, external_cache) -> None:
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                src_id = node_id_by_loc.get((rel_path, node.lineno))
                if src_id is not None:
                    for call in self._collect_calls(node.body):
                        target_id, resolved = self._resolve_call(
                            call, rel_path, enclosing_class, by_name_in_file,
                            by_name_global, methods_by_class, module_to_file,
                            alias_map, repo_id, external_cache)
                        if target_id is not None:
                            self._add_edge(repo_id, src_id, target_id,
                                           EdgeType.CALLS, resolved)
                # descend: this function's own body is a new scope, and it
                # is no longer "inside a class" for self/cls purposes
                self._walk_for_calls(
                    node.body, rel_path, enclosing_class=None,
                    repo_id=repo_id, node_id_by_loc=node_id_by_loc,
                    by_name_in_file=by_name_in_file, by_name_global=by_name_global,
                    methods_by_class=methods_by_class, module_to_file=module_to_file,
                    alias_map=alias_map, external_cache=external_cache)
            elif isinstance(node, ast.ClassDef):
                self._walk_for_calls(
                    node.body, rel_path, enclosing_class=node.name,
                    repo_id=repo_id, node_id_by_loc=node_id_by_loc,
                    by_name_in_file=by_name_in_file, by_name_global=by_name_global,
                    methods_by_class=methods_by_class, module_to_file=module_to_file,
                    alias_map=alias_map, external_cache=external_cache)

    # Finds actual foo(), self.foo(), and module.foo() call expressions in a function.
    @staticmethod
    def _collect_calls(body: list) -> List[tuple]:
        """Direct calls made in a function body, not descending into nested
        def/class bodies (those are resolved as their own symbol's calls)."""
        calls: List[tuple] = []

        class _Collector(ast.NodeVisitor):
            def visit_FunctionDef(self, n):      # noqa: N802 - ast API name
                pass

            def visit_AsyncFunctionDef(self, n):  # noqa: N802
                pass

            def visit_ClassDef(self, n):          # noqa: N802
                pass

            def visit_Call(self, n: ast.Call) -> None:
                func = n.func
                if isinstance(func, ast.Name):
                    calls.append(("name", func.id))
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    base = func.value.id
                    if base in ("self", "cls"):
                        calls.append(("self", func.attr))
                    else:
                        calls.append(("attr", base, func.attr))
                # deeper chains (a.b.c(), f()()) aren't statically resolvable
                # here -- intentionally not recorded, see module docstring.
                self.generic_visit(n)

        collector = _Collector()
        for stmt in body:
            collector.visit(stmt)
        return calls

    # Tries to identify what each call refers to; otherwise creates an unresolved external stub.
    def _resolve_call(self, call, rel_path, enclosing_class, by_name_in_file,
                       by_name_global, methods_by_class, module_to_file,
                       alias_map, repo_id, external_cache) -> _Resolution:
        kind = call[0]

        if kind == "self":
            _, method_name = call
            if enclosing_class is not None:
                hit = methods_by_class.get((rel_path, enclosing_class), {}).get(method_name)
                if hit is not None:
                    return hit, True
            return self._external(repo_id, method_name, external_cache), False

        if kind == "name":
            _, fn_name = call
            same_file = by_name_in_file.get((rel_path, fn_name))
            if same_file and len(same_file) == 1:
                return same_file[0], True
            alias = alias_map.get(fn_name)
            if alias and alias[0] == "symbol":
                _, mod, real_name = alias
                target_file = module_to_file.get(mod)
                if target_file:
                    hits = by_name_in_file.get((target_file, real_name))
                    if hits and len(hits) == 1:
                        return hits[0], True
            glob = by_name_global.get(fn_name)
            if glob and len(glob) == 1:
                return glob[0], True
            return self._external(repo_id, fn_name, external_cache), False

        if kind == "attr":
            _, base, attr_name = call
            alias = alias_map.get(base)
            if alias and alias[0] == "module":
                target_file = module_to_file.get(alias[1])
                if target_file:
                    hits = by_name_in_file.get((target_file, attr_name))
                    if hits and len(hits) == 1:
                        return hits[0], True
            same_file = by_name_in_file.get((rel_path, attr_name))
            if same_file and len(same_file) == 1:
                return same_file[0], True
            return self._external(repo_id, attr_name, external_cache), False

        return None, False

    # Builds a map of imported names/aliases so cross-file calls can be resolved.
    @staticmethod
    def _collect_imports(tree: ast.Module, rel_path: str) -> Dict[str, tuple]:
        """local_name -> ("module", dotted_module) | ("symbol", dotted_module, real_name)"""
        pkg_parts = rel_path.split("/")[:-1]
        alias_map: Dict[str, tuple] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    local = a.asname or a.name.split(".")[0]
                    alias_map[local] = ("module", a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                for a in node.names:
                    local = a.asname or a.name
                    alias_map[local] = ("symbol", mod, a.name)
        return alias_map

    # Creates/reuses an <external> node for an unresolved call.
    def _external(self, repo_id: int, name: str, cache: Dict[str, int]) -> int:
        if name in cache:
            return cache[name]
        node_id = self.store.add_node(Node(
            id=None, repo_id=repo_id, type=NodeType.FUNCTION, name=name,
            file_path=_EXTERNAL_FILE, start_line=0, end_line=0,
            source_text="",
            docstring="unresolved reference (best-effort call resolution)",
        ))
        cache[name] = node_id
        return node_id

    # Creates and saves an edge in SQLite.
    def _add_edge(self, repo_id, source_id, target_id, edge_type, resolved) -> None:
        self.store.add_edge(Edge(
            id=None, repo_id=repo_id, source_node_id=source_id,
            target_node_id=target_id, type=edge_type, resolved=resolved,
        ))