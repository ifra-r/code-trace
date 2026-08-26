"""
CodeGraph Agent — frozen data model (design doc §4).

Contract:
- Every citation anywhere in the system is a Node id.
- Resolving a node id always yields file_path + start_line + end_line.
- Unresolved calls are kept as Edges with resolved=False, never dropped.

This module is stdlib-only: core domain logic must not depend on any
concrete library (no networkx, no pydantic, no anthropic).

Edge direction convention: source --type--> target
  CALLS:     source function/method calls target
  IMPORTS:   source file imports target file/module
  INHERITS:  source class inherits from target class
  CONTAINS:  source (file/class) contains target (class/function/method)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class NodeType(str, enum.Enum):
    """Values are EXACTLY the strings stored in the SQLite `nodes.type` column."""

    FILE = "file"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"


class EdgeType(str, enum.Enum):
    """Values are EXACTLY the strings stored in the SQLite `edges.type` column."""

    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    CONTAINS = "contains"


@dataclass(frozen=True)
class Repo:
    id: Optional[int]              # None until persisted
    name: str
    source_url: str
    local_path: str
    indexed_at: Optional[str] = None   # ISO timestamp, set by the store


@dataclass(frozen=True)
class Node:
    """One indexable unit of code. id is None => not yet persisted."""

    id: Optional[int]
    repo_id: int
    type: NodeType
    name: str
    file_path: str                 # repo-relative path, e.g. "requests/sessions.py"
    start_line: int                # 1-based, inclusive
    end_line: int                  # 1-based, inclusive
    source_text: str = ""
    docstring: str = ""


@dataclass(frozen=True)
class Edge:
    id: Optional[int]
    repo_id: int
    source_node_id: int
    target_node_id: int
    type: EdgeType
    resolved: bool = False         # False = unresolved call target; kept, not dropped


@dataclass
class Symbol:
    """
    Raw extraction result from a Parser — pre-persistence, so no id/repo_id.
    parent_name: enclosing class/function name (None for top-level). Needed
    later for call resolution of methods (same-name methods across classes).
    """

    type: NodeType
    name: str
    file_path: str
    start_line: int
    end_line: int
    source_text: str = ""
    docstring: str = ""
    parent_name: Optional[str] = None


@dataclass(frozen=True)
class Subgraph:
    """Library-free subgraph container. traversal.py lifts this into networkx."""

    nodes: tuple                   # tuple[Node, ...]
    edges: tuple                   # tuple[Edge, ...]