"""Vectorise the wall mask into a graph of centrelines and key points.

The mask says which pixels are wall; this says where the walls *run* and where
they change direction. It thins the mask to a one-pixel centreline, splits that
into runs between nodes, and simplifies each run into a polyline. The surviving
vertices are the key points:

    corner    two walls meet at an angle - the wall turns here
    junction  three or more walls meet - a T or a cross
    endpoint  one wall arrives - a free end, typically a door jamb

Corners are also reported mid-run, where a wall bends without anything else
meeting it.

Loose ends are reported as they are found. Bridging them to whatever they
nearly touch was tried and removed: it closed the network, but every join was
an invention, and the graph no longer said where the walls actually stop.

Thinning comes from `cv2.ximgproc`, which is why the project depends on the
opencv-contrib build rather than the plain one.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import cv2
import numpy as np

from ..config import Config

NEIGHBOURS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Node:
    """A key point on the wall network."""

    id: int
    x: int
    y: int
    kind: str                       # endpoint | corner | junction
    degree: int
    angle_deg: float = 0.0          # direction change, for corners

    def to_dict(self) -> Dict:
        out = {"id": self.id, "point_px": [self.x, self.y],
               "kind": self.kind, "degree": self.degree}
        if self.kind == "corner":
            out["angle_deg"] = round(self.angle_deg, 1)
        return out


@dataclass
class Edge:
    """A wall run between two nodes, as a simplified polyline."""

    id: int
    start: int
    end: int
    polyline_px: List[List[int]] = field(default_factory=list)
    length_px: float = 0.0

    def to_dict(self) -> Dict:
        return {"id": self.id, "from": self.start, "to": self.end,
                "polyline_px": self.polyline_px,
                "length_px": round(self.length_px, 1)}


class WallGraph:
    """Centrelines, nodes and corners extracted from a wall mask."""

    def __init__(self, nodes: List[Node], edges: List[Edge], skeleton: np.ndarray):
        self.nodes = nodes
        self.edges = edges
        self.skeleton = skeleton

    @property
    def corners(self) -> List[Node]:
        return [n for n in self.nodes if n.kind == "corner"]

    @property
    def junctions(self) -> List[Node]:
        return [n for n in self.nodes if n.kind == "junction"]

    @property
    def endpoints(self) -> List[Node]:
        return [n for n in self.nodes if n.kind == "endpoint"]

    def to_dict(self) -> Dict:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "corner_count": len(self.corners),
            "junction_count": len(self.junctions),
            "endpoint_count": len(self.endpoints),
            "total_length_px": round(sum(e.length_px for e in self.edges), 1),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


    # -- construction -------------------------------------------------------

    @classmethod
    def from_mask(cls, walls: np.ndarray, scale: float, cfg: Config) -> "WallGraph":
        skeleton = thin(walls)
        skeleton = prune_spurs(skeleton, max(4, int(round(cfg.graph_prune_frac * scale))))

        nodes, edges = cls._trace(skeleton)
        nodes, edges = cls._contract(nodes, edges, cfg.graph_merge_frac * scale)
        nodes, edges = cls._drop_stubs(nodes, edges, cfg.graph_stub_frac * scale)
        nodes, edges = cls._dissolve(nodes, edges)
        return cls._finish(nodes, edges, skeleton, scale, cfg)

    # Raw tracing -----------------------------------------------------------

    @staticmethod
    def _trace(skeleton):
        """Nodes at branch points and free ends, edges along the runs between.

        Only the branch pixels themselves are lifted out of the skeleton. An
        earlier version dilated them first to merge clusters, which on a plan
        with thin walls claimed 47% of the skeleton as "node" and left the runs
        between as disconnected stubs - the graph fell apart. Merging is a graph
        operation now (see _contract), not a pixel one.
        """
        height, width = skeleton.shape
        degrees = _degree(skeleton)

        branch = ((degrees >= 3) & (skeleton > 0)).astype(np.uint8)
        count, branch_of, _, centroids = cv2.connectedComponentsWithStats(branch, 8)

        nodes = {i: [float(centroids[i][0]), float(centroids[i][1])]
                 for i in range(1, count)}

        free_of = {}
        for y, x in zip(*np.nonzero((degrees == 1) & (skeleton > 0))):
            nid = len(nodes) + 1
            nodes[nid] = [float(x), float(y)]
            free_of[(int(y), int(x))] = nid

        runs = ((skeleton > 0) & (branch == 0)).astype(np.uint8)
        run_count, run_labels, run_stats, _ = cv2.connectedComponentsWithStats(runs, 8)

        edges = []
        for j in range(1, run_count):
            if run_stats[j, cv2.CC_STAT_AREA] < 1:
                continue
            path = _order_run(list(zip(*np.nonzero(run_labels == j))))
            ends = []
            for tip in (path[0], path[-1]):
                nid = free_of.get(tip)
                if nid is None:
                    nid = _adjacent_branch(tip, branch_of, height, width)
                ends.append(nid)
            if ends[0] is None or ends[1] is None or ends[0] == ends[1]:
                continue
            edges.append({"a": ends[0], "b": ends[1], "path": path})
        return nodes, edges

    # Graph simplification --------------------------------------------------

    @staticmethod
    def _contract(nodes, edges, max_length):
        """Merge node pairs joined by a very short edge.

        Thinning a thick junction leaves several branch points a few pixels
        apart. They are one real corner, so the edge between them collapses and
        the nodes become one at their midpoint.
        """
        parent = {n: n for n in nodes}

        def find(n):
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n

        for edge in edges:
            if _path_length(edge["path"]) <= max_length:
                ra, rb = find(edge["a"]), find(edge["b"])
                if ra != rb:
                    parent[rb] = ra

        groups = {}
        for n in nodes:
            groups.setdefault(find(n), []).append(n)

        merged = {}
        for root, members in groups.items():
            merged[root] = [sum(nodes[m][0] for m in members) / len(members),
                            sum(nodes[m][1] for m in members) / len(members)]

        kept = []
        for edge in edges:
            ra, rb = find(edge["a"]), find(edge["b"])
            if ra != rb:
                kept.append({"a": ra, "b": rb, "path": edge["path"]})
        return merged, kept

    @staticmethod
    def _drop_stubs(nodes, edges, min_length):
        """Remove short dead ends left after contraction.

        A stub is an edge whose far end nothing else touches. Short ones are
        thinning artefacts around thick junctions, and each was being reported
        as a spurious free wall end.
        """
        while True:
            degree = {n: 0 for n in nodes}
            for edge in edges:
                degree[edge["a"]] += 1
                degree[edge["b"]] += 1

            doomed = next(
                (i for i, e in enumerate(edges)
                 if (degree[e["a"]] == 1 or degree[e["b"]] == 1)
                 and _path_length(e["path"]) < min_length),
                None)
            if doomed is None:
                break
            edges.pop(doomed)

        used = {e["a"] for e in edges} | {e["b"] for e in edges}
        return {n: p for n, p in nodes.items() if n in used}, edges

    @staticmethod
    def _dissolve(nodes, edges):
        """Join the two runs either side of a pass-through node.

        A node with exactly two runs is not a topological feature: the wall
        simply continues, whether or not it bends. Concatenating the runs leaves
        nodes that mean something - junctions and free ends - and gives longer,
        continuous wall runs. Any bend along the way is still reported, as a
        corner found on the merged run.

        Dropping such nodes instead of dissolving them was a bug: the edges went
        on referencing ids that no longer existed, and after renumbering those
        ids silently pointed at unrelated nodes.
        """
        while True:
            touching = {n: [] for n in nodes}
            for i, edge in enumerate(edges):
                touching[edge["a"]].append(i)
                touching[edge["b"]].append(i)

            victim = next((n for n, es in touching.items()
                           if len(es) == 2 and es[0] != es[1]), None)
            if victim is None:
                break

            i, j = touching[victim]
            first, second = edges[i], edges[j]
            here = nodes[victim]

            left = first["path"]
            if _dist(left[0], (here[1], here[0])) < _dist(left[-1], (here[1], here[0])):
                left = left[::-1]                       # run must arrive at the node
            right = second["path"]
            if _dist(right[-1], (here[1], here[0])) < _dist(right[0], (here[1], here[0])):
                right = right[::-1]                     # and leave from it

            far_a = first["b"] if first["a"] == victim else first["a"]
            far_b = second["b"] if second["a"] == victim else second["a"]

            edges = [e for k, e in enumerate(edges) if k not in (i, j)]
            if far_a != far_b:
                edges.append({"a": far_a, "b": far_b, "path": left + right})
            nodes = {n: p for n, p in nodes.items() if n != victim}
        return nodes, edges

    # Output ----------------------------------------------------------------

    @classmethod
    def _finish(cls, raw_nodes, raw_edges, skeleton, scale, cfg) -> "WallGraph":
        """Renumber, simplify each run, and classify every node."""
        ids = {old: i + 1 for i, old in enumerate(sorted(raw_nodes))}
        epsilon = max(1.5, cfg.graph_simplify_frac * scale)

        edges = []
        incident = {i: [] for i in ids.values()}
        loose = []
        for raw in raw_edges:
            a, b = ids[raw["a"]], ids[raw["b"]]
            ax, ay = raw_nodes[raw["a"]]
            bx, by = raw_nodes[raw["b"]]

            path = raw["path"]
            # Orient the run so it starts at node a, then anchor both ends on the
            # node centres so the drawn graph actually joins up.
            if (_dist(path[0], (ay, ax)) + _dist(path[-1], (by, bx))
                    > _dist(path[-1], (ay, ax)) + _dist(path[0], (by, bx))):
                path = path[::-1]
            interior = [path[i] for i in _rdp(path, epsilon)]
            poly = [(ay, ax)] + interior + [(by, bx)]

            incident[a].append(_unit(poly[0], poly[1]))
            incident[b].append(_unit(poly[-1], poly[-2]))
            edges.append(Edge(id=len(edges) + 1, start=a, end=b,
                              polyline_px=[[int(round(x)), int(round(y))]
                                           for y, x in poly],
                              length_px=_path_length(poly)))

            for k in range(1, len(poly) - 1):
                angle = _turn(poly[k - 1], poly[k], poly[k + 1])
                if angle >= cfg.graph_corner_deg:
                    loose.append((poly[k], angle))

        nodes = []
        for old, new in ids.items():
            x, y = raw_nodes[old]
            directions = incident[new]
            degree = len(directions)
            angle = 0.0
            if degree <= 1:
                kind = "endpoint"
            elif degree == 2:
                cosine = float(np.clip(np.dot(directions[0], directions[1]), -1, 1))
                angle = 180.0 - float(np.degrees(np.arccos(cosine)))
                kind = "corner"
            else:
                kind = "junction"
            nodes.append(Node(id=new, x=int(round(x)), y=int(round(y)),
                              kind=kind, degree=degree, angle_deg=angle))

        for (y, x), angle in loose:
            nodes.append(Node(id=0, x=int(round(x)), y=int(round(y)),
                              kind="corner", degree=2, angle_deg=angle))

        nodes, moved = _merge_close(nodes, cfg.graph_merge_frac * scale)
        where = {n.id: [n.x, n.y] for n in nodes}
        for edge in edges:
            edge.start = moved.get(edge.start, edge.start)
            edge.end = moved.get(edge.end, edge.end)
            # Merging shifts a node; pull its runs along so the graph joins up
            # exactly rather than almost.
            if edge.start in where:
                edge.polyline_px[0] = list(where[edge.start])
            if edge.end in where:
                edge.polyline_px[-1] = list(where[edge.end])
            edge.length_px = _path_length([(y, x) for x, y in edge.polyline_px])
        return cls(nodes, edges, skeleton)


# -- pixel-level helpers ----------------------------------------------------

def _degree(skeleton: np.ndarray) -> np.ndarray:
    """Number of skeleton neighbours each skeleton pixel has."""
    h, w = skeleton.shape
    padded = np.pad(skeleton, 1)
    total = sum(padded[dy:dy + h, dx:dx + w]
                for dy in range(3) for dx in range(3)) - skeleton
    return total * skeleton


def thin(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning: erode the mask to a one-pixel-wide centreline.

    `cv2.ximgproc` lives in opencv-contrib, so this costs the contrib build
    rather than the plain one - the same wheel family and the same `cv2` import,
    just a larger package. Worth it over carrying a hand-rolled version: the
    library one is C++ rather than a Python loop, and thinning is the slowest
    step in the graph stage.

    Checked against the hand-written implementation this replaced: identical
    output, pixel for pixel, on all three sample plans.
    """
    if not hasattr(cv2, "ximgproc"):
        raise ImportError(
            "cv2.ximgproc is missing, so this is the plain opencv build. "
            "Install the contrib one: pip install -r requirements.txt "
            "(opencv-contrib-python-headless), or run via docker compose.")

    thinned = cv2.ximgproc.thinning(
        (mask > 0).astype(np.uint8) * 255,
        thinningType=cv2.ximgproc.THINNING_ZHANGSUEN)
    return (thinned > 0).astype(np.uint8)


def prune_spurs(skeleton: np.ndarray, min_length: int) -> np.ndarray:
    """Drop short dead-end branches.

    Thinning a thick junction throws off small whiskers that are artefacts of
    the mask's shape, not walls. Anything shorter than a real wall stub goes.
    """
    sk = skeleton.copy()
    h, w = sk.shape
    for _ in range(40):
        degrees = _degree(sk)
        removed = False
        for y, x in zip(*np.nonzero((degrees == 1) & (sk > 0))):
            if sk[y, x] == 0:
                continue
            path = [(y, x)]
            cy, cx, prev = y, x, None
            while True:
                nxt = [(cy + dy, cx + dx) for dy, dx in NEIGHBOURS
                       if 0 <= cy + dy < h and 0 <= cx + dx < w
                       and sk[cy + dy, cx + dx] and (cy + dy, cx + dx) != prev]
                if len(nxt) != 1 or len(path) > min_length:
                    break
                prev = (cy, cx)
                cy, cx = nxt[0]
                path.append((cy, cx))
            if len(path) <= min_length:
                for py, px in path:
                    sk[py, px] = 0
                removed = True
        if not removed:
            break
    return sk


# A point where three walls meet outranks a bend, which outranks a loose end:
# when near-duplicates collapse, the most significant reading survives.
_PRIORITY = {"junction": 0, "corner": 1, "endpoint": 2}


def _merge_close(nodes: List[Node], radius: float):
    """Collapse key points that are really one point.

    Thinning scatters a handful of branch pixels around a thick junction, and a
    run that bends right beside one contributes its own corner. Both show up as
    a knot of nodes a few pixels apart, which is why the graph looked
    over-populated. Everything within `radius` becomes a single node, keeping
    the highest-ranking kind in the group.

    Returns the surviving nodes and a map from every old id to the id that
    replaced it, so the edges can be repointed - renumbering without remapping
    silently corrupts the topology.
    """
    ordered = sorted(nodes, key=lambda n: (_PRIORITY.get(n.kind, 3), -n.degree))
    kept: List[Node] = []
    absorbed_by = {}
    for node in ordered:
        host = next((k for k in kept
                     if (node.x - k.x) ** 2 + (node.y - k.y) ** 2 <= radius ** 2), None)
        if host is None:
            kept.append(node)
            host = node
        if node.id:
            absorbed_by[node.id] = host

    for i, node in enumerate(kept, 1):
        node.id = i
    return kept, {old: host.id for old, host in absorbed_by.items()}






def _adjacent_branch(tip, branch_of, height, width):
    """Branch-cluster id touching a run's tip, if any."""
    y, x = tip
    for dy, dx in NEIGHBOURS + [(0, 0)]:
        yy, xx = y + dy, x + dx
        if 0 <= yy < height and 0 <= xx < width and branch_of[yy, xx]:
            return int(branch_of[yy, xx])
    return None


def _dist(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _path_length(path) -> float:
    return sum(_dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def _order_run(pixels) -> List[Tuple[int, int]]:
    """Walk an unordered run of pixels into a path."""
    remaining = set(pixels)
    ends = [p for p in pixels
            if sum((p[0] + dy, p[1] + dx) in remaining for dy, dx in NEIGHBOURS) <= 1]
    current = ends[0] if ends else pixels[0]

    path, seen = [current], {current}
    while True:
        nxt = [(current[0] + dy, current[1] + dx) for dy, dx in NEIGHBOURS
               if (current[0] + dy, current[1] + dx) in remaining
               and (current[0] + dy, current[1] + dx) not in seen]
        if not nxt:
            break
        current = nxt[0]
        path.append(current)
        seen.add(current)
    return path


def _rdp(points, epsilon: float) -> List[int]:
    """Ramer-Douglas-Peucker, returning the indices that survive."""
    pts = np.asarray(points, float)
    if len(pts) < 3:
        return list(range(len(pts)))

    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        span = float(np.linalg.norm(ab))
        block = pts[i + 1:j] - a
        if span < 1e-9:
            dist = np.linalg.norm(block, axis=1)
        else:
            dist = np.abs(ab[0] * block[:, 1] - ab[1] * block[:, 0]) / span
        k = int(np.argmax(dist))
        if dist[k] > epsilon:
            m = i + 1 + k
            keep.add(m)
            stack.extend([(i, m), (m, j)])
    return sorted(keep)


def _unit(a, b) -> Tuple[float, float]:
    """Unit vector from a to b, in (y, x) order."""
    v = np.array([b[0] - a[0], b[1] - a[1]], float)
    n = float(np.linalg.norm(v))
    return (0.0, 0.0) if n < 1e-9 else tuple(v / n)


def _turn(a, b, c) -> float:
    """How far the path turns at b, in degrees. 0 is straight on."""
    v1, v2 = _unit(a, b), _unit(b, c)
    if not any(v1) or not any(v2):
        return 0.0
    cosine = float(np.clip(v1[0] * v2[0] + v1[1] * v2[1], -1, 1))
    return float(np.degrees(np.arccos(cosine)))
