"""SQLite-backed GraphStore — one DB file per indexed repo: .codegraph/graph.db.
Write side lands in step 4, search in step 5, full query contract in step 6.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from core.graph.models import Edge, EdgeType, Node, NodeType, Repo, Subgraph
from core.retrieval.interfaces import Direction, GraphStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source_url  TEXT NOT NULL UNIQUE,
    local_path  TEXT NOT NULL,
    indexed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id),
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    source_text TEXT NOT NULL DEFAULT '',
    docstring   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_nodes_repo ON nodes(repo_id);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_name ON nodes(repo_id, name);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_file ON nodes(repo_id, file_path);

CREATE TABLE IF NOT EXISTS edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repos(id),
    source_node_id  INTEGER NOT NULL REFERENCES nodes(id),
    target_node_id  INTEGER NOT NULL REFERENCES nodes(id),
    type            TEXT NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id, type);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id, type);
"""


class SqliteGraphStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- frozen query contract ----

    def get_node(self, node_id: int) -> Optional[Node]:
        raise NotImplementedError("implemented in build step 6")

    def get_neighbors(
        self,
        node_id: int,
        edge_type: Optional[EdgeType] = None,
        *,
        direction: Direction = "out",
    ) -> List[Node]:
        raise NotImplementedError("implemented in build step 6")

    def get_subgraph(self, node_ids: Sequence[int]) -> Subgraph:
        raise NotImplementedError("implemented in build step 6")

    def search_symbols(self, query: str, limit: int = 10) -> List[Node]:
        raise NotImplementedError("implemented in build steps 5-6")

    # ---- pipeline-facing write side ----

    def add_repo(self, name: str, source_url: str, local_path: str) -> int:
        indexed_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO repos (name, source_url, local_path, indexed_at) "
            "VALUES (?, ?, ?, ?)",
            (name, source_url, local_path, indexed_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_repo_by_url(self, source_url: str) -> Optional[Repo]:
        row = self._conn.execute(
            "SELECT id, name, source_url, local_path, indexed_at "
            "FROM repos WHERE source_url = ?",
            (source_url,),
        ).fetchone()
        if row is None:
            return None
        return Repo(id=row[0], name=row[1], source_url=row[2],
                     local_path=row[3], indexed_at=row[4])

    def add_node(self, node: Node) -> int:
        cur = self._conn.execute(
            "INSERT INTO nodes (repo_id, type, name, file_path, start_line, "
            "end_line, source_text, docstring) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (node.repo_id, node.type.value, node.name, node.file_path,
             node.start_line, node.end_line, node.source_text, node.docstring),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_edge(self, edge: Edge) -> int:
        cur = self._conn.execute(
            "INSERT INTO edges (repo_id, source_node_id, target_node_id, "
            "type, resolved) VALUES (?, ?, ?, ?, ?)",
            (edge.repo_id, edge.source_node_id, edge.target_node_id,
             edge.type.value, int(edge.resolved)),
        )
        self._conn.commit()
        return cur.lastrowid