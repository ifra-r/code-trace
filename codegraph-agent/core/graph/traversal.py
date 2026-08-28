"""Bounded DFS along resolved `calls` edges. Implemented in step 8.

Deliberately NOT networkx-backed, despite the original stub docstring and
§3's stack table listing networkx for traversal: a BFS with a parent map,
reconstructing the path only once the goal is found, gives the same
shortest-path guarantee as building a subgraph and calling
networkx.shortest_path, without materializing graph structure this bounded
operation doesn't need. Revisit if a later step (e.g. react-flow's fuller
graph visualization in step 11) wants real graph-library features.
"""

from typing import Dict, List, Optional

from core.graph.models import EdgeType, Node
from core.retrieval.interfaces import GraphStore

_EXTERNAL_FILE = "<external>"  # keep in sync with core/graph/graph_builder.py


def find_call_path(
    store: GraphStore,
    start_id: int,
    goal_id: Optional[int] = None,
    max_depth: int = 12,
) -> List[Node]:
    """Ordered list of Nodes from start_id to goal_id (inclusive), or a
    bounded exploratory walk from start_id if goal_id is None.

    External stub nodes (unresolved call targets, file_path="<external>")
    are never included: nothing is ever recorded as calling FROM a stub
    (add_edge never uses one as a source), so they're dead ends by
    construction and carry no real file/line evidence worth tracing through.

    goal_id given: BFS outward from start_id using a parent map, stopping as
    soon as goal_id is discovered (guarantees shortest path in an unweighted
    graph) or max_depth hops are exhausted. [] if no path exists within that
    bound, or if start_id/goal_id don't resolve to real nodes.

    goal_id absent: deterministic greedy walk -- at each step, follow the
    lowest node_id real (non-external) callee not already visited in this
    path, so cycles/recursion terminate instead of looping forever. Stops at
    a dead end or max_depth.
    """
    start = store.get_node(start_id)
    if start is None or start.file_path == _EXTERNAL_FILE:
        return []

    if goal_id is not None:
        return _bfs_shortest_path(store, start_id, goal_id, max_depth)
    return _greedy_walk(store, start_id, max_depth)


def _real_callees(store: GraphStore, node_id: int) -> List[Node]:
    return [n for n in store.get_neighbors(node_id, EdgeType.CALLS, direction="out")
            if n.file_path != _EXTERNAL_FILE]


def _bfs_shortest_path(store: GraphStore, start_id: int, goal_id: int,
                        max_depth: int) -> List[Node]:
    if start_id == goal_id:
        node = store.get_node(start_id)
        return [node] if node is not None else []

    goal = store.get_node(goal_id)
    if goal is None or goal.file_path == _EXTERNAL_FILE:
        return []

    parent: Dict[int, Optional[int]] = {start_id: None}
    frontier = [start_id]
    depth = 0

    while frontier and depth < max_depth:
        next_frontier: List[int] = []
        for current_id in frontier:
            for callee in _real_callees(store, current_id):
                if callee.id in parent:
                    continue  # already reached at an earlier (<=) depth
                parent[callee.id] = current_id
                if callee.id == goal_id:
                    return _reconstruct(store, parent, goal_id)
                next_frontier.append(callee.id)
        frontier = next_frontier
        depth += 1

    return []  # goal not reached within max_depth


def _reconstruct(store: GraphStore, parent: Dict[int, Optional[int]],
                  goal_id: int) -> List[Node]:
    chain: List[int] = []
    node_id: Optional[int] = goal_id
    while node_id is not None:
        chain.append(node_id)
        node_id = parent[node_id]
    chain.reverse()
    nodes = [store.get_node(nid) for nid in chain]
    return [n for n in nodes if n is not None]


def _greedy_walk(store: GraphStore, start_id: int, max_depth: int) -> List[Node]:
    path_ids = [start_id]
    visited = {start_id}
    current_id = start_id

    for _ in range(max_depth):
        callees = [c for c in _real_callees(store, current_id) if c.id not in visited]
        if not callees:
            break
        nxt = min(callees, key=lambda n: n.id)  # deterministic tie-break
        path_ids.append(nxt.id)
        visited.add(nxt.id)
        current_id = nxt.id

    nodes = [store.get_node(nid) for nid in path_ids]
    return [n for n in nodes if n is not None]