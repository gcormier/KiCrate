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
    cutouts: list = field(default_factory=list)
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


PCB_LABEL = re.compile(r"(SUGGESTED|RECOMMENDED|MAX\.?(IMUM)?)\b.{0,30}\b(PC|PCB|P\.C\.|PRINTED CIRCUIT)\s*(BOARD|LAYOUT|SIZE)", re.I)


def find_labelled_board(doc, outer_size: tuple[float, float]) -> Extraction | None:
    """The PCB view a drawing labels itself ("SUGGESTED PC BOARD LAYOUT", "MAX. PCB SIZE", ...):
    the closest closed outline to the label that fits inside the box, plus its holes."""
    msp = doc.modelspace()
    labels = [p for t, p in _text_items(msp) if PCB_LABEL.search(t)]
    if not labels:
        return None
    loops = [lp for lp in closed_loops(_segments(msp)) if not _is_round(lp)]
    circles = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in msp.query("CIRCLE")
               if not SKIP_LAYERS.search(e.dxf.get("layer", ""))]
    L, W = sorted(outer_size, reverse=True)
    best = None
    for pos in labels:
        for f in dict.fromkeys([25.4, 1.0, *view_scales(doc)]):
            for lp in loops:
                x0, y0, x1, y1 = lp.bbox
                w, h = sorted(((x1 - x0) * f, (y1 - y0) * f), reverse=True)
                if not (0.4 * L < w < L and 0.4 * W < h < W):  # a board fits inside the box
                    continue
                gap = math.dist(pos, (min(max(pos[0], x0), x1), min(max(pos[1], y0), y1)))
                if gap * f < 0.25 * L and (best is None or gap * f < best[0]):
                    best = (gap * f, lp, f, (w, h))
    if not best:
        return None
    _, lp, f, size = best
    ext = _board_from([lp], circles, size, [f])
    if ext:
        ext.notes.insert(0, "PCB outline and holes from the drawing's own labelled PCB view.")
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


# --- Boxes that publish posts but no PCB outline (e.g. Bud PN series) -------------------------

BOSS_TEXT = re.compile(r"BOSS|ACCEPTS\b.*\bSCREW", re.I)  # Bud: "... BOSS WITH M3 ...", Hammond: "ACCEPTS #4 ... SCREW"
PLACES = re.compile(r"\((\d+)\)\s*PLACES|\((\d+)\s*X\)", re.I)
FOOTNOTE = re.compile(r"^(\(\*+\)|\*+)\s")
FOOTNOTE_REF = re.compile(r"(\(\*+\))")
THREAD = re.compile(r"\b(M\d+(?:\.\d+)?)\b|(#\d+)")
# Clearance hole for each thread (ISO 273 medium / common practice), mm.
CLEARANCE_HOLE = {"M2": 2.2, "M2.5": 2.7, "M3": 3.2, "M4": 4.3, "M5": 5.3, "#2": 2.7, "#4": 3.2, "#6": 3.5, "#8": 4.3}


def _text_items(msp):
    """Notes as (text, position). A block reference (AutoCAD leader notes, SolidWorks SW_NOTE
    blocks) is one note; loose single-line TEXT stacked into a paragraph is joined."""

    def plain(e):
        return " ".join((e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).split())

    def flat(entities, depth=0):
        for e in entities:
            if e.dxftype() in ("MTEXT", "TEXT"):
                yield e
            elif e.dxftype() == "INSERT" and depth < 4:
                try:
                    yield from flat(e.virtual_entities(), depth + 1)
                except Exception:  # noqa: BLE001 - unusual block transforms: skip the block
                    pass

    loose = []
    for e in msp:
        if e.dxftype() == "INSERT":
            parts = [x for x in flat([e]) if plain(x)]
            if parts:
                top = max(parts, key=lambda x: x.dxf.insert.y)
                yield " ".join(plain(x) for x in parts), (top.dxf.insert.x, top.dxf.insert.y)
        elif e.dxftype() == "MTEXT":
            yield plain(e), (e.dxf.insert.x, e.dxf.insert.y)
        elif e.dxftype() == "TEXT" and plain(e):
            loose.append(e)
    # Paragraphs drafted as one TEXT per line: same left edge, line pitch under ~2 text heights.
    loose.sort(key=lambda e: (round(e.dxf.insert.x, 3), -e.dxf.insert.y))
    para = []
    for e in loose + [None]:
        if para and e is not None:
            last = para[-1]
            h = max(last.dxf.height, e.dxf.height)
            if abs(e.dxf.insert.x - last.dxf.insert.x) < 0.2 * h and 0 < last.dxf.insert.y - e.dxf.insert.y < 2.2 * h:
                para.append(e)
                continue
        if para:
            yield " ".join(plain(x) for x in para), (para[0].dxf.insert.x, para[0].dxf.insert.y)
        para = [e] if e is not None else []


def _continuous(e, doc) -> bool:
    """Solid (visible-edge) linework only: dashed/phantom/centre/hidden lines are not walls."""
    lt = (e.dxf.get("linetype") or "BYLAYER").upper()
    if lt == "BYLAYER":
        layer = doc.layers.get(e.dxf.get("layer", "0")) if doc.layers.has_entry(e.dxf.get("layer", "0")) else None
        lt = (layer.dxf.get("linetype") if layer else "CONTINUOUS").upper()
    return lt in ("CONTINUOUS", "BYBLOCK")


def _linework(msp, view, skip=SKIP_LAYERS):
    from shapely.geometry import LineString

    doc = msp.doc
    out = []

    def add(pts):
        if len(pts) >= 2:
            g = LineString(pts)
            if g.length > 0 and view.intersects(g):
                out.append(g)

    for e in msp:
        if skip.search(e.dxf.get("layer", "")) or not _continuous(e, doc):
            continue
        t = e.dxftype()
        if t == "LINE":
            add([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])
        elif t == "LWPOLYLINE":
            for v in e.virtual_entities():
                if v.dxftype() == "LINE":
                    add([(v.dxf.start.x, v.dxf.start.y), (v.dxf.end.x, v.dxf.end.y)])
                else:
                    add([(p.x, p.y) for p in v.flattening(0.002)])
        elif t in ("ARC", "CIRCLE", "SPLINE", "ELLIPSE"):
            add([(p.x, p.y) for p in e.flattening(0.002)])
    return out


DIA = re.compile(r"(\d*\.\d+|\d+)\s*DIA", re.I)


def find_posts_boards(doc, outer_size: tuple[float, float], clearance: float = 2.0,
                      inner_size: tuple[float, float] | None = None, cap_on_posts: bool = False) -> list[tuple[str, Extraction]]:
    """Boards for boxes whose drawing shows PCB posts ("... BOSS WITH M3 ...") but no outline.

    One result per post callout, as (id, Extraction). The body (top) view is the closed outline
    matching `outer_size` (mm; the drawing may be in inches). Posts are the circle groups the
    callout's leader points at, or else whose diameter the callout states. The board is the
    enclosed floor shrunk by `clearance` from walls, ribs and other bosses, capped at
    `inner_size` minus the clearance (centred on the posts with `cap_on_posts`, else on the
    floor), and always covering its posts.
    """
    from shapely.geometry import Point, Polygon, box
    from shapely.ops import unary_union

    msp = doc.modelspace()
    texts = list(_text_items(msp))
    # Footnotes: "BOSS (*) WITH ..." takes its screw size from the note starting "(*) ...".
    footnotes = {m.group(1): t for t, _ in texts if (m := FOOTNOTE.match(t))}
    callouts = []
    for t, p in texts:
        if FOOTNOTE.match(t) or not BOSS_TEXT.search(t):
            continue
        for mark in FOOTNOTE_REF.findall(t):
            if mark in footnotes and not THREAD.search(t):
                t = f"{t} {footnotes[mark]}"
        if THREAD.search(t):
            callouts.append((t, p))
    if not callouts:
        return []
    circles = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in msp.query("CIRCLE")
               if not SKIP_LAYERS.search(e.dxf.get("layer", ""))]
    leaders = [[(v[0], v[1]) for v in e.vertices] for e in msp.query("LEADER")]

    def ring_set(center):
        return tuple(sorted(round(r, 4) for x, y, r in circles if math.dist((x, y), center) < 1e-3))

    # Candidate top views: closed outlines of the outer size, at inch or mm scale.
    want = sorted(outer_size, reverse=True)
    views = []
    for f in (25.4, 1.0):
        for lp in closed_loops(_segments(msp)):
            x0, y0, x1, y1 = lp.bbox
            w, h = (x1 - x0) * f, (y1 - y0) * f
            if abs(max(w, h) - want[0]) < 1.0 and abs(min(w, h) - want[1]) < 1.0:
                views.append((lp, f))
        if views:
            break
    # Views drawn with splines/ellipses don't form line/arc loops: also accept a connected cluster
    # of solid linework of the right size around each callout's leader tip.
    for f in (25.4, 1.0):
        if any(vf == f for _, vf in views):
            continue
        for _, tpos in callouts:
            if not leaders:
                break
            leader = min(leaders, key=lambda vs: min(math.dist(vs[-1], tpos), math.dist(vs[0], tpos)))
            tip = leader[0] if math.dist(leader[-1], tpos) <= math.dist(leader[0], tpos) else leader[-1]
            view = _cluster_view(msp, tip, want, f)
            if view and all(v[0].bbox != view.bbox for v in views):
                views.append((view, f))
    if not views:
        return []

    results = []
    for n, (text, tpos) in enumerate(callouts):
        best = None
        for lp, f in views:
            outline_poly = Polygon(lp.poly)
            inside = [c for c in circles if outline_poly.contains(Point(c[:2]))]
            posts = []
            if leaders:
                leader = min(leaders, key=lambda vs: min(math.dist(vs[-1], tpos), math.dist(vs[0], tpos)))
                tip = leader[0] if math.dist(leader[-1], tpos) <= math.dist(leader[0], tpos) else leader[-1]
                if outline_poly.contains(Point(tip)) and inside:
                    hit = min(inside, key=lambda c: abs(math.dist(c[:2], tip) - c[2]))
                    if abs(math.dist(hit[:2], tip) - hit[2]) < 0.05 * max(want) / f:
                        sig = ring_set(hit[:2])
                        posts = sorted({(round(x, 4), round(y, 4)) for x, y, _ in inside if ring_set((x, y)) == sig})
            if not posts:  # match the diameters the callout states
                dias = [float(d) for d in DIA.findall(text)]
                tol = 0.004 if f == 25.4 else 0.1
                posts = sorted({(round(x, 4), round(y, 4)) for x, y, r in inside if any(abs(2 * r - d) < tol for d in dias)})
                # Several boss kinds can share a diameter: keep the ring pattern the callout counts.
                count = PLACES.search(text)
                if count and len(posts) > int(count.group(1) or count.group(2)):
                    n_want = int(count.group(1) or count.group(2))
                    groups: dict = {}
                    for pt in posts:
                        groups.setdefault(ring_set(pt), []).append(pt)
                    exact = [g for g in groups.values() if len(g) == n_want]
                    posts = exact[0] if len(exact) == 1 else posts
            if posts and (best is None or len(posts) > len(best[2])):
                best = (lp, f, posts)
        if not best:
            continue
        lp, f, posts = best
        ext = _board_over_posts(msp, lp, f, posts, ring_set, text, clearance, inner_size, cap_on_posts)
        if ext and inner_size and not cap_on_posts:
            xs, ys = [p[0] for p in ext.outline.points], [p[1] for p in ext.outline.points]
            if max(xs) - min(xs) > max(inner_size) + 0.01 or max(ys) - min(ys) > min(inner_size) + 0.01:
                continue  # posts outside the floor (lid/cover bosses), not PCB posts
        if ext:
            thread = THREAD.search(text)
            screw = thread.group(1) or thread.group(2)
            kind = "inserts" if "INSERT" in text.upper() else "bosses"
            mid = f"{screw.lower().replace('#', 'no').replace('.', '_')}_{kind}" if len(callouts) > 1 else "main"
            while mid in {m for m, _ in results}:
                mid += "_2"
            results.append((mid, ext))
    return results


def _cluster_view(msp, tip, want, f) -> "Loop | None":
    """Bounding box of the connected solid linework around `tip`, if it has the box's outer size."""
    from shapely.geometry import Point, box
    from shapely.ops import unary_union

    reach = 1.5 * want[0] / f
    window = box(tip[0] - reach, tip[1] - reach, tip[0] + reach, tip[1] + reach)
    eps = 0.15 / f  # bridge small drafting gaps (~0.15 mm)
    blobs = unary_union([g.buffer(eps) for g in _linework(msp, window)])
    for g in getattr(blobs, "geoms", [blobs]):
        x0, y0, x1, y1 = g.bounds
        w, h = (x1 - x0 - 2 * eps) * f, (y1 - y0 - 2 * eps) * f
        if abs(max(w, h) - want[0]) < 0.03 * want[0] and abs(min(w, h) - want[1]) < 0.03 * want[1] \
                and box(*g.bounds).contains(Point(tip)):
            x0, y0, x1, y1 = x0 + eps, y0 + eps, x1 - eps, y1 - eps
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            return Loop([Seg(pts[i - 1], pts[i]) for i in range(4)], pts)
    return None


def _board_over_posts(msp, lp, f, posts, ring_set, text, clearance, inner_size, cap_on_posts=False):
    from shapely.geometry import Point, Polygon, box
    from shapely.ops import unary_union

    x0, y0, x1, y1 = lp.bbox
    pad = 0.02 * max(x1 - x0, y1 - y0)
    view = box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    eps = 0.08 / f  # ~0.08 mm, in drawing units
    walls = unary_union([g.buffer(eps) for g in _linework(msp, view)])
    free = view.difference(walls)
    regions = [g for g in getattr(free, "geoms", [free]) if not g.intersects(view.exterior)]
    if not regions:
        return None
    floor = max(regions, key=lambda g: g.area).buffer(eps)
    cl = clearance / f
    post_pts = [Point(c) for c in posts]
    boss_r = max(max(ring_set(c)) for c in posts)
    # Islands that touch a post (the boss itself, its stiffening fins) belong to the post; the rest
    # (e.g. bosses of another mounting option) are obstacles the board must clear.
    near_posts = unary_union([p.buffer(2 * max(ring_set(c))) for p, c in zip(post_pts, posts)])
    islands = [Polygon(r) for r in floor.interiors if not Polygon(r).intersects(near_posts)]
    # The board rests on its posts (their stiffening fins are lower), so a post merged into a wall is
    # still board area: add each post's boss, grown by the clearance, before shrinking.
    discs = [p.buffer(boss_r, quad_segs=8) for p in post_pts]
    area = unary_union([Polygon(floor.exterior), *(d.buffer(cl) for d in discs)])
    if area.geom_type == "Polygon":  # pockets the discs enclose (fin gaps) are post, not obstacle
        area = Polygon(area.exterior, [r for r in area.interiors if not Polygon(r).intersects(near_posts)])
    board = area.buffer(-cl, quad_segs=4, join_style="round")
    if islands:
        board = board.difference(unary_union(islands).buffer(cl, quad_segs=4))
    if inner_size:  # a size cap: the stated inside size (walls are drafted), or a max PCB size centred on the posts
        fx0, fy0, fx1, fy1 = floor.bounds
        cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
        if cap_on_posts:
            cx, cy = sum(p[0] for p in posts) / len(posts), sum(p[1] for p in posts) / len(posts)
        L, W = sorted(inner_size, reverse=True)
        a, b = (L / f / 2 - cl, W / f / 2 - cl)
        if (fy1 - fy0) > (fx1 - fx0):
            a, b = b, a
        board = board.intersection(unary_union([box(cx - a, cy - b, cx + a, cy + b), *discs]))
    board = unary_union([board, *discs])
    if board.is_empty:
        return None
    if board.geom_type != "Polygon":
        board = max(board.geoms, key=lambda g: g.area)
    from shapely import affinity, make_valid, set_precision

    from .schema import PolygonOutline

    bx0, by0, bx1, by1 = board.bounds
    rotate = (by1 - by0) > (bx1 - bx0)
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2

    def tr(p):
        x, y = (p[0] - cx) * f, (p[1] - cy) * f
        return (round(-y, 2), round(x, 2)) if rotate else (round(x, 2), round(y, 2))

    # To board mm (centred, long side on X), simplify to 0.1 mm, snap to a 0.01 mm grid (stays valid).
    mm = affinity.scale(affinity.translate(board, -cx, -cy), f, f, origin=(0, 0))
    if rotate:
        mm = affinity.rotate(mm, 90, origin=(0, 0))
    mm = make_valid(set_precision(mm.simplify(0.1), 0.01))  # 0.1 mm is ample for a board edge
    if mm.geom_type != "Polygon":
        mm = max((g for g in getattr(mm, "geoms", []) if g.geom_type == "Polygon"), key=lambda g: g.area)
    pts = [(round(x, 2), round(y, 2)) for x, y in list(mm.exterior.coords)[:-1]]
    cutouts = [PolygonOutline(points=[(round(x, 2), round(y, 2)) for x, y in list(r.coords)[:-1]])
               for r in mm.interiors if len(r.coords) > 3]
    if sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts))) < 0:
        pts.reverse()
    thread = THREAD.search(text)
    screw = thread.group(1) or thread.group(2)
    drill = CLEARANCE_HOLE.get(screw, 3.2)
    stated = re.search(r"(\d*\.\d+|\d+)\s*DIA\.?\s*BOSS", text, re.I)
    boss_d = round(float(stated.group(1)) * f, 2) if stated else round(2 * boss_r * f, 2)
    if boss_d <= drill:  # only the screw hole is drawn round (finned boss): no meaningful diameter
        boss_d = None
    holes = [Hole(at=tr(c), drill=drill, boss_diameter=boss_d, screw=screw) for c in posts]
    ext = Extraction(outline=PolygonOutline(points=pts), holes=holes, scale=f, cutouts=cutouts)
    pattern = _as_pattern(holes)
    if pattern:
        ext.hole_pattern = pattern.model_copy(update={"boss_diameter": boss_d, "screw": screw})
        ext.holes = []
    ext.notes += [
        f"Posts from the drawing callout: \"{text}\".",
        f"Outline is the enclosed floor in the drawing shrunk by {clearance:g} mm from walls, ribs and other bosses"
        + (", capped at the stated inside size" if inner_size else "") + "; it always covers its posts "
        "(KiCrate-derived, not a manufacturer outline).",
        f"PCB hole {drill:g} mm is the clearance for {screw}.",
    ]
    return ext
