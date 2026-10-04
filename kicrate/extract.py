"""Pull PCB geometry out of manufacturer CAD drawings (DWG via LibreDWG, or DXF).

The approach is deliberately simple: find a closed loop of lines/arcs whose
size matches the manufacturer's stated max PCB size (at 1:1 or at any view
scale used by the drawing's dimensions), then take the circles inside it as
holes. Anything not found is left for a human.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf

from .geometry import arc_through, point_in_polygon
from .schema import Hole, HolePattern, PathOutline, RectOutline

SKIP_LAYERS = re.compile(r"hidden|center|centre|dim|hatch|text|border|title|phantom", re.I)
TOL = 0.3  # mm when matching sizes
KEY = 0.01  # endpoint snapping grid


def read_cad(path: Path) -> ezdxf.document.Drawing:
    path = Path(path)
    if path.suffix.lower() == ".dxf":
        return ezdxf.readfile(path)
    if path.suffix.lower() != ".dwg":
        raise ValueError(f"unsupported CAD file: {path}")
    tool = shutil.which("dwg2dxf")
    if not tool:
        raise RuntimeError("dwg2dxf (LibreDWG) is required to read DWG files")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.dxf"
        subprocess.run([tool, "-y", "-o", str(out), str(path)], capture_output=True, check=False, timeout=300)
        if not out.exists():
            raise RuntimeError(f"dwg2dxf failed on {path}")
        return ezdxf.readfile(out)


@dataclass
class Seg:
    a: tuple[float, float]
    b: tuple[float, float]
    mid: tuple[float, float] | None = None  # set for arcs


@dataclass
class Loop:
    segs: list[Seg]
    poly: list[tuple[float, float]]

    @property
    def bbox(self):
        xs, ys = [p[0] for p in self.poly], [p[1] for p in self.poly]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Extraction:
    outline: RectOutline | PathOutline
    holes: list[Hole] = field(default_factory=list)
    hole_pattern: HolePattern | None = None
    scale: float = 1.0
    notes: list[str] = field(default_factory=list)


def _k(p) -> tuple[int, int]:
    return (round(p[0] / KEY), round(p[1] / KEY))


def _segments(msp) -> list[Seg]:
    segs = []
    for e in msp:
        if SKIP_LAYERS.search(e.dxf.get("layer", "")) or SKIP_LAYERS.search(e.dxf.get("linetype", "") or ""):
            continue
        t = e.dxftype()
        if t == "LINE":
            a, b = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
            if math.dist(a, b) > KEY:
                segs.append(Seg(a, b))
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            am = (a0 + a1) / 2
            pt = lambda a: (c.x + r * math.cos(a), c.y + r * math.sin(a))  # noqa: E731
            segs.append(Seg(pt(a0), pt(a1), pt(am)))
        elif t == "LWPOLYLINE":
            for sub in e.virtual_entities():
                if sub.dxftype() == "LINE":
                    segs.append(Seg((sub.dxf.start.x, sub.dxf.start.y), (sub.dxf.end.x, sub.dxf.end.y)))
    return segs


def closed_loops(segs: list[Seg]) -> list[Loop]:
    """Components where every endpoint joins exactly two segments."""
    at: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, s in enumerate(segs):
        at[_k(s.a)].append(i)
        at[_k(s.b)].append(i)
    used: set[int] = set()
    loops = []
    for start in range(len(segs)):
        if start in used:
            continue
        ok, i, node = True, start, _k(segs[start].b)
        first = _k(segs[start].a)
        seen = {start}
        oriented = [segs[start]]
        while True:
            nxt = [j for j in at[node] if j != i]
            if len(at[node]) != 2 or not nxt:
                ok = False
                break
            j = nxt[0]
            if j == start:
                break
            if j in seen:
                ok = False
                break
            s = segs[j]
            if _k(s.a) != node:  # orient along the walk
                s = Seg(s.b, s.a, s.mid)
            oriented.append(s)
            seen.add(j)
            i, node = j, _k(s.b)
        used |= seen
        if ok and node == first and len(oriented) >= 2:
            loops.append(Loop(oriented, _flatten(oriented)))
    return loops


def _flatten(segs: list[Seg]) -> list[tuple[float, float]]:
    pts = []
    for s in segs:
        pts.append(s.a)
        if s.mid:
            pts.append(s.mid)
    return pts


def view_scales(doc) -> list[float]:
    """Real-size factors used by the drawing's views (from DIMENSION dimlfac overrides)."""
    scales = {1.0}
    for d in doc.modelspace().query("DIMENSION"):
        f = d.override().get("dimlfac")
        if f and abs(f - 1) > 1e-6:
            scales.add(round(f, 6))
    return sorted(scales, key=lambda f: abs(f - 1))


def find_board(doc, size: tuple[float, float]) -> Extraction | None:
    """Loop matching `size` (either orientation) and the holes inside it."""
    msp = doc.modelspace()
    loops = closed_loops(_segments(msp))
    circles = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in msp.query("CIRCLE")
               if not SKIP_LAYERS.search(e.dxf.get("layer", ""))]
    want = sorted(size, reverse=True)
    best = None
    for f in view_scales(doc):
        for lp in loops:
            x0, y0, x1, y1 = lp.bbox
            w, h = (x1 - x0) * f, (y1 - y0) * f
            if abs(max(w, h) - want[0]) > TOL or abs(min(w, h) - want[1]) > TOL:
                continue
            inside = [c for c in circles if point_in_polygon((c[0], c[1]), lp.poly) and c[2] * f < want[1] / 4]
            score = (len(inside), f == 1.0)
            if best is None or score > best[0]:
                best = (score, lp, f, inside, h > w)
    if not best:
        return None
    _, lp, f, inside, rotate = best
    x0, y0, x1, y1 = lp.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def tr(p):  # drawing -> board coords, longer side along X
        x, y = (p[0] - cx) * f, (p[1] - cy) * f
        return (round(-y, 3), round(x, 3)) if rotate else (round(x, 3), round(y, 3))

    ext = Extraction(outline=_outline(lp, tr), scale=f)
    holes: dict[tuple[float, float], float] = {}
    for x, y, r in inside:  # keep the smallest of concentric circles
        c = tr((x, y))
        holes[c] = min(holes.get(c, math.inf), round(2 * r * f, 3))
    ext.holes = [Hole(at=c, drill=d) for c, d in sorted(holes.items())]
    ext.hole_pattern = _as_pattern(ext.holes)
    if ext.hole_pattern:
        ext.holes = []
    if f != 1.0:
        ext.notes.append(f"PCB view drawn at {1 / f:g}:1; scaled to real size.")
    return ext


def _outline(lp: Loop, tr) -> RectOutline | PathOutline:
    pts = [tr(s.a) for s in lp.segs]
    if len(lp.segs) == 4 and not any(s.mid for s in lp.segs):
        xs, ys = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
        if len(xs) == 2 and len(ys) == 2:
            return RectOutline(size=(round(xs[1] - xs[0], 3), round(ys[1] - ys[0], 3)))
    nodes = []
    for s in lp.segs:
        b = tr(s.b)
        nodes.append((*b, *tr(s.mid)) if s.mid else b)
    # Validate arcs are well formed.
    for i, n in enumerate(nodes):
        if len(n) == 4:
            arc_through(nodes[i - 1][:2], n[2:], n[:2])
    return PathOutline(nodes=nodes)


def _as_pattern(holes: list[Hole]) -> HolePattern | None:
    if len(holes) != 4 or len({h.drill for h in holes}) != 1:
        return None
    xs = sorted({abs(h.at[0]) for h in holes})
    ys = sorted({abs(h.at[1]) for h in holes})
    pts = {(round(h.at[0], 2), round(h.at[1], 2)) for h in holes}
    if len(xs) == 1 and len(ys) == 1 and pts == {(sx * round(xs[0], 2), sy * round(ys[0], 2)) for sx in (-1, 1) for sy in (-1, 1)}:
        return HolePattern(pitch=(round(2 * xs[0], 3), round(2 * ys[0], 3)), drill=holes[0].drill)
    return None


INSIDE = re.compile(r"INSIDE\s*\\P\s*(LENGTH|WIDTH|HEIGHT)|INSIDE\s+(LENGTH|WIDTH|HEIGHT)", re.I)


def inside_dims(doc) -> dict[str, float]:
    """Dimensions whose text labels them INSIDE LENGTH/WIDTH/HEIGHT."""
    out = {}
    for d in doc.modelspace().query("DIMENSION"):
        m = INSIDE.search(d.dxf.get("text", "") or "")
        if m:
            f = d.override().get("dimlfac") or 1.0
            out[(m.group(1) or m.group(2)).lower()] = round(d.get_measurement() * f, 3)
    return out
