"""Turn outlines into closed paths of lines and arcs (math coords, Y up)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schema import Keepout, PathOutline, PolygonOutline, RectOutline

Pt = tuple[float, float]


@dataclass(frozen=True)
class Line:
    start: Pt
    end: Pt


@dataclass(frozen=True)
class Arc:
    start: Pt
    mid: Pt
    end: Pt
    center: Pt
    radius: float

    @property
    def ccw(self) -> bool:
        return _cross(_sub(self.mid, self.start), _sub(self.end, self.mid)) > 0


Segment = Line | Arc


def _sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Pt, b: Pt) -> Pt:
    return (a[0] + b[0], a[1] + b[1])


def _mul(a: Pt, k: float) -> Pt:
    return (a[0] * k, a[1] * k)


def _norm(a: Pt) -> Pt:
    n = math.hypot(*a)
    return (a[0] / n, a[1] / n)


def _cross(a: Pt, b: Pt) -> float:
    return a[0] * b[1] - a[1] * b[0]


def outline_vertices(outline: RectOutline | PolygonOutline) -> list[tuple[Pt, float]]:
    """Counter-clockwise vertices with a fillet radius each."""
    if isinstance(outline, PolygonOutline):
        return [((p[0], p[1]), p[2] if len(p) > 2 else 0.0) for p in outline.points]
    w, h = outline.size[0] / 2, outline.size[1] / 2
    r = outline.corner_radius
    n = outline.corner_notches
    verts: list[tuple[Pt, float]] = []
    # Corners in CCW order starting bottom-right, each followed by the edge leaving it.
    for (sx, sy), edge in zip(((1, -1), (1, 1), (-1, 1), (-1, -1)), ("x_max", "y_max", "x_min", "y_min")):
        if not n:
            verts.append(((sx * w, sy * h), r))
        else:
            nx, ny = n.size
            a = ((sx * w, sy * (h - ny)), r)  # on the X-facing edge
            b = ((sx * (w - nx), sy * (h - ny)), n.inner_radius)
            c = ((sx * (w - nx), sy * h), n.outer_radius)  # on the Y-facing edge
            # Walking CCW, quadrants (1,-1) and (-1,1) meet the Y-facing edge first.
            verts.extend([c, b, a] if sx * sy < 0 else [a, b, c])
        verts.extend(_edge_notch_vertices(outline, edge, w, h))
    return verts


# For each edge: (fixed axis value sign, travel direction along the free axis, free axis index)
_EDGES = {"x_max": (1, 1, 1), "y_max": (1, -1, 0), "x_min": (-1, -1, 1), "y_min": (-1, 1, 0)}


def _edge_notch_vertices(outline: RectOutline, edge: str, w: float, h: float) -> list[tuple[Pt, float]]:
    side, travel, free = _EDGES[edge]
    fixed = side * (w if free == 1 else h)
    out: list[tuple[Pt, float]] = []
    for en in sorted((e for e in outline.edge_notches if e.edge == edge), key=lambda e: travel * e.at):
        inner = fixed - side * en.depth
        for along, across, rad in (
            (en.at - travel * en.width / 2, fixed, en.outer_radius),
            (en.at - travel * en.width / 2, inner, en.inner_radius),
            (en.at + travel * en.width / 2, inner, en.inner_radius),
            (en.at + travel * en.width / 2, fixed, en.outer_radius),
        ):
            out.append(((across, along) if free == 1 else (along, across), rad))
    return out


def fillet_path(verts: list[tuple[Pt, float]]) -> list[Segment]:
    """Closed path through `verts`, replacing each vertex with an arc of its radius."""
    k = len(verts)
    corners: list[tuple[Pt, Pt, Arc | None]] = []  # (entry point, exit point, arc)
    for i, (v, r) in enumerate(verts):
        p0, p1 = verts[i - 1][0], verts[(i + 1) % k][0]
        if r <= 0:
            corners.append((v, v, None))
            continue
        u0, u1 = _norm(_sub(p0, v)), _norm(_sub(p1, v))
        cos_a = max(-1.0, min(1.0, u0[0] * u1[0] + u0[1] * u1[1]))
        half = math.acos(cos_a) / 2
        d = r / math.tan(half)
        t0, t1 = _add(v, _mul(u0, d)), _add(v, _mul(u1, d))
        bis = _norm(_add(u0, u1))
        c = _add(v, _mul(bis, r / math.sin(half)))
        mid = _sub(c, _mul(bis, r))
        corners.append((t0, t1, Arc(t0, mid, t1, c, r)))
    for i in range(k):
        v, nxt = verts[i][0], verts[(i + 1) % k][0]
        used = math.dist(v, corners[i][1]) + math.dist(nxt, corners[(i + 1) % k][0])
        if used > math.dist(v, nxt) + 1e-9:
            raise ValueError(f"fillet radii too large on edge {v} -> {nxt}")
    segs: list[Segment] = []
    for i, (_, exit_pt, arc) in enumerate(corners):
        if arc:
            segs.append(arc)
        nxt_entry = corners[(i + 1) % k][0]
        if math.dist(exit_pt, nxt_entry) > 1e-9:
            segs.append(Line(exit_pt, nxt_entry))
    return segs


def arc_through(start: Pt, mid: Pt, end: Pt) -> Arc:
    """Arc defined by three points on it."""
    (ax, ay), (bx, by), (cx, cy) = start, mid, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        raise ValueError(f"arc points are collinear: {start} {mid} {end}")
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return Arc(start, mid, end, (ux, uy), math.dist((ux, uy), start))


def reverse(s: Segment) -> Segment:
    if isinstance(s, Line):
        return Line(s.end, s.start)
    return Arc(s.end, s.mid, s.start, s.center, s.radius)


def _path_segments(outline: PathOutline) -> list[Segment]:
    segs: list[Segment] = []
    nodes = outline.nodes
    for i, n in enumerate(nodes):
        prev = nodes[i - 1]
        start, end = (prev[0], prev[1]), (n[0], n[1])
        if len(n) == 4:
            segs.append(arc_through(start, (n[2], n[3]), end))
        elif math.dist(start, end) > 1e-9:
            segs.append(Line(start, end))
    return segs


def outline_path(outline: RectOutline | PolygonOutline | PathOutline) -> list[Segment]:
    if isinstance(outline, PathOutline):
        segs = _path_segments(outline)
        poly = flatten(segs)
        if sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly))) < 0:
            segs = [reverse(s) for s in reversed(segs)]  # normalise to counter-clockwise
        return segs
    return fillet_path(outline_vertices(outline))


def flatten(segs: list[Segment], step_deg: float = 5.0) -> list[Pt]:
    """Polygon approximation (for containment checks and keepout zones)."""
    pts: list[Pt] = []
    for s in segs:
        if isinstance(s, Line):
            pts.append(s.start)
            continue
        a0 = math.atan2(s.start[1] - s.center[1], s.start[0] - s.center[0])
        a1 = math.atan2(s.end[1] - s.center[1], s.end[0] - s.center[0])
        sweep = a1 - a0
        if s.ccw and sweep < 0:
            sweep += 2 * math.pi
        if not s.ccw and sweep > 0:
            sweep -= 2 * math.pi
        n = max(2, int(abs(math.degrees(sweep)) / step_deg))
        for j in range(n):
            a = a0 + sweep * j / n
            pts.append((s.center[0] + s.radius * math.cos(a), s.center[1] + s.radius * math.sin(a)))
    return pts


def circle_polygon(cx: float, cy: float, r: float, n: int = 32) -> list[Pt]:
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def point_in_polygon(p: Pt, poly: list[Pt]) -> bool:
    x, y = p
    inside = False
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i - 1], poly[i]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def distance_to_polygon(p: Pt, poly: list[Pt]) -> float:
    best = math.inf
    for i in range(len(poly)):
        a, b = poly[i - 1], poly[i]
        ab = _sub(b, a)
        L2 = ab[0] ** 2 + ab[1] ** 2
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / L2))
        best = min(best, math.dist(p, _add(a, _mul(ab, t))))
    return best


def bbox(poly: list[Pt]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def keepout_polygon(k: Keepout, board: list[Pt]) -> list[Pt]:
    if k.circle:
        return circle_polygon(k.circle[0], k.circle[1], k.circle[2] / 2)
    if k.rect:
        x0, y0, x1, y1 = k.rect
    else:
        x0, y0, x1, y1 = bbox(board)
        w = k.width
        x0, y0, x1, y1 = {
            "x_min": (x0, y0, x0 + w, y1),
            "x_max": (x1 - w, y0, x1, y1),
            "y_min": (x0, y0, x1, y0 + w),
            "y_max": (x0, y1 - w, x1, y1),
        }[k.edge]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
