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


def _k(p, key: float = KEY) -> tuple[int, int]:
    return (round(p[0] / key), round(p[1] / key))


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


def closed_loops(segs: list[Seg], key: float = KEY) -> list[Loop]:
    """Components where every endpoint joins exactly two segments."""
    at: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, s in enumerate(segs):
        at[_k(s.a, key)].append(i)
        at[_k(s.b, key)].append(i)
    used: set[int] = set()
    loops = []
    for start in range(len(segs)):
        if start in used:
            continue
        ok, i, node = True, start, _k(segs[start].b, key)
        first = _k(segs[start].a, key)
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
            if _k(s.a, key) != node:  # orient along the walk
                s = Seg(s.b, s.a, s.mid)
            oriented.append(s)
            seen.add(j)
            i, node = j, _k(s.b, key)
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
    return _board_from(loops, circles, size, view_scales(doc))


def _board_from(loops: list[Loop], circles, size, scales, tol: float = TOL, digits: int = 3) -> Extraction | None:
    """Pick the loop matching `size` at one of `scales` (drawing units -> mm), plus holes inside it."""
    want = sorted(size, reverse=True)
    best, matches = None, set()
    for f in scales:
        for lp in loops:
            x0, y0, x1, y1 = lp.bbox
            w, h = (x1 - x0) * f, (y1 - y0) * f
            if abs(max(w, h) - want[0]) > tol or abs(min(w, h) - want[1]) > tol:
                continue
            inside = [c for c in circles if point_in_polygon((c[0], c[1]), lp.poly) and c[2] * f < want[1] / 4]
            matches.add(id(lp))
            score = (len(inside), f == scales[0])
            if best is None or score > best[0]:
                best = (score, lp, f, inside, h > w)
    if not best:
        return None
    _, lp, f, inside, rotate = best
    x0, y0, x1, y1 = lp.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def tr(p):  # drawing -> board coords, longer side along X
        x, y = (p[0] - cx) * f, (p[1] - cy) * f
        return (round(-y, digits), round(x, digits)) if rotate else (round(x, digits), round(y, digits))

    ext = Extraction(outline=_outline(lp, tr), scale=f)
    holes: dict[tuple[float, float], float] = {}
    for x, y, r in inside:  # keep the smallest of (near-)concentric circles
        c = tr((x, y))
        c = next((k for k in holes if math.dist(k, c) < 0.1), c)
        holes[c] = min(holes.get(c, math.inf), round(2 * r * f, digits))
    ext.holes = [Hole(at=c, drill=d) for c, d in sorted(holes.items())]
    ext.hole_pattern = _as_pattern(ext.holes)
    if ext.hole_pattern:
        ext.holes = []
    if len(matches) > 1:
        ext.notes.append(f"Drawing shows {len(matches)} boards of this size; took the one with the most holes ({len(inside)}).")
    if f != scales[0]:
        ext.notes.append(f"PCB view drawn at {scales[0] / f:.3g}:1; scaled to real size.")
    return ext


PT = 25.4 / 72  # PDF point in mm


def find_board_pdf(pdf: bytes | Path, size: tuple[float, float]) -> Extraction | None:
    """Same search over a vector PDF drawing (curves arrive flattened into short lines)."""
    import pymupdf

    doc = pymupdf.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes) else pymupdf.open(pdf)
    # Hammond labels the board page "MAX RECOMMENDED PCB SIZE"; other views (inside bottom) can
    # have an inner wall of the same size, so try labelled pages first.
    pcb_page = re.compile(r"MAX(IMUM)?\s+(RECOMMENDED\s+)?PCB\s+SIZE|MAX\s+SIZE\s+PCB", re.I)
    pages = sorted(doc, key=lambda pg: not pcb_page.search(pg.get_text()))
    for page in pages:
        segs = []
        for path in page.get_drawings():
            for it in path["items"]:
                if it[0] == "l":
                    a, b = (it[1].x, -it[1].y), (it[2].x, -it[2].y)  # PDF y points down
                    if math.dist(a, b) > 1e-6:
                        segs.append(Seg(a, b))
                elif it[0] == "c":
                    p0, p1, p2, p3 = it[1:5]
                    mid = (0.125 * (p0.x + 3 * p1.x + 3 * p2.x + p3.x), -0.125 * (p0.y + 3 * p1.y + 3 * p2.y + p3.y))
                    segs.append(Seg((p0.x, -p0.y), (p3.x, -p3.y), mid))
                elif it[0] == "re":
                    r = it[1]
                    c = [(r.x0, -r.y0), (r.x1, -r.y0), (r.x1, -r.y1), (r.x0, -r.y1)]
                    segs += [Seg(c[i - 1], c[i]) for i in range(4)]
        loops = closed_loops(segs, key=0.05)
        circles, others = [], []
        for lp in loops:
            x0, y0, x1, y1 = lp.bbox
            w, h = x1 - x0, y1 - y0
            if (len(lp.segs) >= 8 or all(sg.mid for sg in lp.segs)) and w > 0 and abs(w - h) < 0.03 * w and _is_round(lp):
                # Flattened circle: vertices lie on the circle, so their mean is the centre.
                pts = [s.a for s in lp.segs]
                c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                circles.append((*c, sum(math.dist(p, c) for p in pts) / len(pts)))
            else:
                others.append(_rebuild_arcs(lp))
        # 1:1 first; otherwise a view scale implied by a loop of the right proportions that has holes in it.
        scales = [PT]
        want = sorted(size, reverse=True)
        for lp in others:
            x0, y0, x1, y1 = lp.bbox
            long_, short_ = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
            if short_ > 0 and abs(long_ / short_ - want[0] / want[1]) < 0.01 * want[0] / want[1] \
                    and any(point_in_polygon(c[:2], lp.poly) for c in circles):
                scales.append(want[0] / long_)
        ext = _board_from(others, circles, size, scales, tol=0.4, digits=2)
        if ext:
            # Flattening noise is ~0.02 mm; drawings dimension holes in 0.05 mm steps.
            snap = lambda d: round(round(d / 0.05) * 0.05, 2) if abs(d - round(d / 0.05) * 0.05) <= 0.02 else d  # noqa: E731
            ext.holes = [h.model_copy(update={"drill": snap(h.drill)}) for h in ext.holes]
            if ext.hole_pattern:
                ext.hole_pattern = ext.hole_pattern.model_copy(update={"drill": snap(ext.hole_pattern.drill),
                                                                      "pitch": tuple(round(v, 2) for v in ext.hole_pattern.pitch)})
            ext.notes.append(f"From the vector PDF drawing (page {page.number + 1}).")
            return ext
    return None


def _is_round(lp: Loop) -> bool:
    x0, y0, x1, y1 = lp.bbox
    c, r = ((x0 + x1) / 2, (y0 + y1) / 2), (x1 - x0 + y1 - y0) / 4
    return all(abs(math.dist(p, c) - r) < 0.05 * r for p in lp.poly)


def _rebuild_arcs(lp: Loop, short: float = 1.2 / PT) -> Loop:
    """Turn runs of short flattened segments back into arcs (start, mid, end)."""
    segs = lp.segs
    n = len(segs)
    is_short = [s.mid is None and math.dist(s.a, s.b) < short for s in segs]
    if all(is_short) or not any(is_short):
        return lp
    # Rotate so we start at a long segment; then group consecutive short ones.
    k = is_short.index(False)
    segs, is_short = segs[k:] + segs[:k], is_short[k:] + is_short[:k]
    out: list[Seg] = []
    i = 0
    while i < n:
        if not is_short[i]:
            out.append(segs[i])
            i += 1
            continue
        j = i
        while j < n and is_short[j]:
            j += 1
        run = segs[i:j]
        pts = [run[0].a] + [s.b for s in run]
        a, b, m = pts[0], pts[-1], pts[len(pts) // 2]
        chord = math.dist(a, b)
        sag = abs((b[0] - a[0]) * (m[1] - a[1]) - (b[1] - a[1]) * (m[0] - a[0])) / chord if chord else 0
        if len(run) >= 2 and sag > 0.02 * chord:
            out.append(Seg(a, b, m))
        else:
            out.append(Seg(a, b))  # straight after all
        i = j
    return Loop(out, _flatten(out))


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


def _as_pattern(holes: list[Hole], tol: float = 0.05) -> HolePattern | None:
    """Four equal holes symmetric about the centre (within `tol`) -> a centred pattern."""
    if len(holes) != 4 or max(h.drill for h in holes) - min(h.drill for h in holes) > tol:
        return None
    ax = [abs(h.at[0]) for h in holes]
    ay = [abs(h.at[1]) for h in holes]
    if max(ax) - min(ax) > tol or max(ay) - min(ay) > tol:
        return None
    if {(h.at[0] > 0, h.at[1] > 0) for h in holes} != {(a, b) for a in (False, True) for b in (False, True)}:
        return None
    hx, hy = sum(ax) / 4, sum(ay) / 4
    return HolePattern(pitch=(round(2 * hx, 3), round(2 * hy, 3)), drill=round(sum(h.drill for h in holes) / 4, 3))


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
