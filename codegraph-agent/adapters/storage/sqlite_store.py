"""SQLite-backed GraphStore — one DB file per indexed repo: .codegraph/graph.db.
Write side landed in step 4. `search_symbols` (BM25 + name matching) landed
in step 5. Step 6 (this implementation): the full frozen query contract --
get_node, get_neighbors, get_subgraph, search_symbols. From here on, /api,
/core/agent, and /mcp touch only this interface, never the DB directly.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from core.graph.models import Edge, EdgeType, Node, NodeType, Repo, Subgraph
from core.retrieval.bm25_retriever import BM25Retriever
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

_EXTERNAL_FILE = "<external>"  # keep in sync with core/graph/graph_builder.py


class SqliteGraphStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Lazily built, in-memory, invalidated on every add_node -- see
        # _get_or_build_bm25. Cheap enough at hackathon scale to just rebuild
        # rather than track incremental diffs.
        self._bm25: Optional[BM25Retriever] = None

    # ---- frozen query contract ----

    def get_node(self, node_id: int) -> Optional[Node]:
        """Resolve a node id to a full Node (incl. file/lines). None if
        unknown. This is the whole reason node ids are stable and
        resolvable per §4 -- every citation ultimately calls this."""
        rows = self._fetch_nodes_by_ids([node_id])
        return rows[0] if rows else None

    def get_neighbors(
        self,
        node_id: int,
        edge_type: Optional[EdgeType] = None,
        *,
        direction: Direction = "out",
    ) -> List[Node]:
        """
        direction="out"  -> callees   (edges leaving node_id)
        direction="in"   -> callers   (edges entering node_id)
        direction="both" -> union of both, de-duplicated, out-then-in order
        """
        ids: List[int] = []
        seen = set()

        def collect(sql: str, params: list) -> None:
            for (nid,) in self._conn.execute(sql, params).fetchall():
                if nid not in seen:
                    seen.add(nid)
                    ids.append(nid)

        if direction in ("out", "both"):
            sql = "SELECT target_node_id FROM edges WHERE source_node_id=?"
            params = [node_id]
            if edge_type is not None:
                sql += " AND type=?"
                params.append(edge_type.value)
            collect(sql + " ORDER BY id", params)

        if direction in ("in", "both"):
            sql = "SELECT source_node_id FROM edges WHERE target_node_id=?"
            params = [node_id]
            if edge_type is not None:
                sql += " AND type=?"
                params.append(edge_type.value)
            collect(sql + " ORDER BY id", params)

        return self._fetch_nodes_by_ids(ids)

    def get_subgraph(self, node_ids: Sequence[int]) -> Subgraph:
        """Induced subgraph over node_ids (known ids only) + edges among
        them. Unknown ids are silently dropped, not an error -- callers
        (e.g. flow_tracer building a react-flow payload) typically pass ids
        gathered from several sources and shouldn't have to pre-validate."""
        nodes = self._fetch_nodes_by_ids(node_ids)
        known_ids = [n.id for n in nodes]
        if not known_ids:
            return Subgraph(nodes=tuple(), edges=tuple())

        placeholders = ",".join("?" * len(known_ids))
        rows = self._conn.execute(
            f"SELECT id, repo_id, source_node_id, target_node_id, type, resolved "
            f"FROM edges WHERE source_node_id IN ({placeholders}) "
            f"AND target_node_id IN ({placeholders})",
            known_ids + known_ids,
        ).fetchall()
        edges = tuple(
            Edge(id=r[0], repo_id=r[1], source_node_id=r[2], target_node_id=r[3],
                 type=EdgeType(r[4]), resolved=bool(r[5]))
            for r in rows
        )
        return Subgraph(nodes=tuple(nodes), edges=edges)

    def search_symbols(self, query: str, limit: int = 10) -> List[Node]:
        """BM25 (qualified_name+name+docstring+source_text) with exact/
        prefix/substring name-match boosting -- see bm25_retriever.py.
        External stub nodes (unresolved call targets, file_path="<external>")
        are never indexed: they aren't real source and have nothing to cite."""
        retriever = self._get_or_build_bm25()
        hits = retriever.search(query, limit=limit)
        ids = [node_id for node_id, _score in hits]
        return self._fetch_nodes_by_ids(ids)

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
        self._bm25 = None  # corpus changed -- rebuild lazily on next search
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

    # ---- internals ----

    def _fetch_nodes_by_ids(self, ids: Sequence[int]) -> List[Node]:
        """Hydrates node ids to Nodes, preserving the input order (so BM25
        rank order and get_neighbors' edge-id order both survive). Unknown
        ids are silently dropped -- shared by get_node/get_neighbors/
        get_subgraph/search_symbols so there's exactly one row->Node path."""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT id, repo_id, type, name, file_path, start_line, end_line, "
            f"source_text, docstring FROM nodes WHERE id IN ({placeholders})",
            list(ids),
        ).fetchall()
        by_id = {row[0]: row for row in rows}
        return [self._row_to_node(by_id[i]) for i in ids if i in by_id]

    def _get_or_build_bm25(self) -> BM25Retriever:
        if self._bm25 is None:
            rows = self._conn.execute(
                "SELECT id, name, docstring, source_text FROM nodes "
                "WHERE file_path != ?", (_EXTERNAL_FILE,),
            ).fetchall()
            docs = []
            for node_id, name, docstring, source_text in rows:
                qualified = self._qualified_name(node_id, name)
                text = f"{qualified} {name} {docstring} {source_text}"
                docs.append((node_id, name, text))
            self._bm25 = BM25Retriever(docs)
        return self._bm25

    def _qualified_name(self, node_id: int, name: str) -> str:
        """Walks CONTAINS edges upward (method -> class -> outer class...)
        to build a dotted qualified name like "Session.request", stopping at
        the file. Lets BM25/exact-match boosting find "Session.request" or
        plain "Session" queries without the caller pre-computing nesting."""
        parts = [name]
        current_id = node_id
        while True:
            row = self._conn.execute(
                """
                SELECT n.id, n.name, n.type FROM edges e
                JOIN nodes n ON n.id = e.source_node_id
                WHERE e.target_node_id = ? AND e.type = 'contains'
                LIMIT 1
                """,
                (current_id,),
            ).fetchone()
            if row is None:
                break
            parent_id, parent_name, parent_type = row
            if parent_type == "file":
                break
            parts.append(parent_name)
            current_id = parent_id
        return ".".join(reversed(parts))

    @staticmethod
    def _row_to_node(row) -> Node:
        (node_id, repo_id, type_str, name, file_path, start_line, end_line,
         source_text, docstring) = row
        return Node(
            id=node_id, repo_id=repo_id, type=NodeType(type_str), name=name,
            file_path=file_path, start_line=start_line, end_line=end_line,
            source_text=source_text, docstring=docstring,
        )