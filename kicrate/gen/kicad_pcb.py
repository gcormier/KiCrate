"""Write a KiCad 10 .kicad_pcb for one PCB mount."""

from __future__ import annotations

import copy
import uuid

from .. import __version__, footprints, sexpr
from ..geometry import Arc, Line, circle_polygon, flatten, keepout_polygon, outline_path
from ..schema import Enclosure, Hole, Panel, PcbMount
from ..sexpr import Sym

FILE_VERSION = 20260206  # KiCad 10.0
ORIGIN = (148.5, 105.0)  # board centre on an A4 sheet

_LAYERS = [
    (0, "F.Cu", "signal"), (2, "B.Cu", "signal"),
    (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user"), (15, "B.Paste", "user"),
    (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user"), (3, "B.Mask", "user"),
    (17, "Dwgs.User", "user", "User.Drawings"), (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"), (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user"), (27, "Margin", "user"),
    (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user"), (33, "B.Fab", "user"),
]


class _Ids:
    """Deterministic UUIDs so regenerated boards diff cleanly."""

    def __init__(self, seed: str):
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, f"kicrate:{seed}")
        self.n = 0

    def __call__(self) -> list:
        self.n += 1
        return [Sym("uuid"), str(uuid.uuid5(self.ns, str(self.n)))]


def _xy(p) -> tuple[float, float]:
    return (round(ORIGIN[0] + p[0], 6), round(ORIGIN[1] - p[1], 6))


def _stroke(w: float) -> list:
    return [Sym("stroke"), [Sym("width"), w], [Sym("type"), Sym("default")]]


def _segments(segs, layer: str, width: float, ids: _Ids) -> list[list]:
    out = []
    for s in segs:
        if isinstance(s, Line):
            out.append([Sym("gr_line"), [Sym("start"), *_xy(s.start)], [Sym("end"), *_xy(s.end)],
                        _stroke(width), [Sym("layer"), layer], ids()])
        elif isinstance(s, Arc):
            out.append([Sym("gr_arc"), [Sym("start"), *_xy(s.start)], [Sym("mid"), *_xy(s.mid)], [Sym("end"), *_xy(s.end)],
                        _stroke(width), [Sym("layer"), layer], ids()])
    return out


def _rect(x0, y0, x1, y1, layer, width, ids) -> list[list]:
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return _segments([Line(pts[i - 1], pts[i]) for i in range(4)], layer, width, ids)


def _text(txt: str, at, layer: str, ids: _Ids, size: float = 1.0, justify_left: bool = False) -> list:
    effects = [Sym("effects"), [Sym("font"), [Sym("size"), size, size], [Sym("thickness"), round(size * 0.15, 3)]]]
    if justify_left:
        effects.append([Sym("justify"), Sym("left")])
    return [Sym("gr_text"), txt, [Sym("at"), *_xy(at), 0], [Sym("layer"), layer], ids(), effects]


def _rule_area(name: str, side: str, poly, ids: _Ids) -> list:
    layers = {"top": ["F.Cu"], "bottom": ["B.Cu"], "both": ["F.Cu", "B.Cu"]}[side]
    layer_node = [Sym("layer"), layers[0]] if len(layers) == 1 else [Sym("layers"), *layers]
    return [
        Sym("zone"), layer_node, ids(), [Sym("name"), name], [Sym("hatch"), Sym("edge"), 0.5],
        [Sym("connect_pads"), [Sym("clearance"), 0]],
        [Sym("min_thickness"), 0.25],
        [Sym("keepout"), [Sym("tracks"), Sym("allowed")], [Sym("vias"), Sym("allowed")], [Sym("pads"), Sym("allowed")],
         [Sym("copperpour"), Sym("allowed")], [Sym("footprints"), Sym("not_allowed")]],
        [Sym("placement"), [Sym("enabled"), Sym("no")], [Sym("sheetname"), ""]],
        [Sym("fill"), [Sym("thermal_gap"), 0.5], [Sym("thermal_bridge_width"), 0.5]],
        [Sym("polygon"), [Sym("pts"), *[[Sym("xy"), *_xy(p)] for p in poly]]],
    ]


def _place_footprint(lib_id: str, fp: list, ref: str, at, ids: _Ids) -> list:
    fp = copy.deepcopy(fp)
    body = [c for c in fp[2:] if not (isinstance(c, list) and c and c[0] in ("version", "generator", "generator_version", "layer"))]
    for c in body:
        if isinstance(c, list) and c[:2] == ["property", "Reference"]:
            c[2] = ref
    return [Sym("footprint"), lib_id, [Sym("locked"), Sym("yes")], [Sym("layer"), "F.Cu"], ids(), [Sym("at"), *_xy(at)], *body]


def _header(title: str, company: str, comments: list[str], thickness: float = 1.6) -> list:
    return [
        Sym("kicad_pcb"),
        [Sym("version"), FILE_VERSION],
        [Sym("generator"), "kicrate"],
        [Sym("generator_version"), __version__],
        [Sym("general"), [Sym("thickness"), thickness], [Sym("legacy_teardrops"), Sym("no")]],
        [Sym("paper"), "A4"],
        [Sym("title_block"), [Sym("title"), title], [Sym("company"), company],
         *[[Sym("comment"), i + 1, c] for i, c in enumerate(comments)]],
        [Sym("layers"), *[[l[0], l[1], Sym(l[2]), *l[3:]] for l in _LAYERS]],
        [Sym("setup"), [Sym("pad_to_mask_clearance"), 0],
         [Sym("aux_axis_origin"), *ORIGIN], [Sym("grid_origin"), *ORIGIN]],
        [Sym("net"), 0, ""],
    ]


def _status(enc: Enclosure) -> str:
    return f"{'verified' if enc.verified else 'UNVERIFIED'} ({enc.provenance})"


def _notes(lines: list[str], x0: float, top: float, ids: _Ids) -> list[list]:
    return [_text(note, (x0, top + 2 * (len(lines) - i)), "Cmts.User", ids, justify_left=True) for i, note in enumerate(lines)]


def board(enc: Enclosure, mount: PcbMount) -> list:
    ids = _Ids(f"{enc.key}/{mount.id}")
    tree = _header(
        f"{enc.mfr.title()} {enc.part} - {mount.id}",
        enc.mfr.title(),
        [f"{enc.series}; {mount.type}; {_status(enc)}", enc.url or "", f"Generated by KiCrate {__version__}. Origin = board centre."],
    )

    segs = outline_path(mount.outline)
    board_poly = flatten(segs)
    tree += _segments(segs, "Edge.Cuts", 0.05, ids)
    for c in mount.cutouts:
        tree += _segments(outline_path(c), "Edge.Cuts", 0.05, ids)

    # Enclosure inside wall for reference.
    if enc.inner:
        w, h = enc.inner.length / 2, enc.inner.width / 2
        tree += _rect(-w, -h, w, h, "Dwgs.User", 0.1, ids)
        tree.append(_text("enclosure inside wall", (-w, h + 1.5), "Dwgs.User", ids, justify_left=True))

    for n, hole in enumerate(mount.all_holes(), 1):
        lib_id, fp = footprints.footprint_for(hole)
        tree.append(_place_footprint(lib_id, fp, f"H{n}", hole.at, ids))
        if hole.boss_diameter:
            tree.append(_rule_area(f"boss_H{n}", "bottom", _circle(hole.at, hole.boss_diameter / 2), ids))
        if hole.head_diameter:
            tree.append(_rule_area(f"screw_head_H{n}", "top", _circle(hole.at, hole.head_diameter / 2), ids))

    for n, k in enumerate(mount.keepouts, 1):
        poly = keepout_polygon(k, board_poly)
        if k.max_height is None:
            tree.append(_rule_area(k.reason or f"keepout_{n}", k.side, poly, ids))
        else:
            tree += _segments([Line(poly[i - 1], poly[i]) for i in range(len(poly))], "Dwgs.User", 0.1, ids)
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            tree.append(_text(f"{k.side} max {k.max_height:g} mm", (cx, cy), "Dwgs.User", ids, size=0.8))

    by_screw: dict[str, list[str]] = {}
    for n, hole in enumerate(mount.all_holes(), 1):
        key = f"dia {hole.drill:g} mm" + (f", {hole.screw}" if hole.screw else "") + (f", boss dia {hole.boss_diameter:g}" if hole.boss_diameter else "")
        by_screw.setdefault(key, []).append(f"H{n}")
    hole_notes = [f"{', '.join(refs)}: {key}" for key, refs in by_screw.items()]
    notes = [n for n in (mount.description, *hole_notes, *mount.notes) if n]
    top = max(max(p[1] for p in board_poly), enc.inner.width / 2 if enc.inner else 0) + 5
    tree += _notes(notes, min(p[0] for p in board_poly), top, ids)
    tree.append([Sym("embedded_fonts"), Sym("no")])
    return tree


def panel_board(enc: Enclosure, panel: Panel) -> list:
    """A panel as a PCB (front-panel style), viewed from outside."""
    ids = _Ids(f"{enc.key}/panel/{panel.id}")
    tree = _header(
        f"{enc.mfr.title()} {enc.part} - panel {panel.id}",
        enc.mfr.title(),
        [f"{enc.series}; faces: {', '.join(panel.faces)}; {_status(enc)}", enc.url or "",
         f"Generated by KiCrate {__version__}. Origin = panel centre, viewed from outside."],
        thickness=panel.thickness or 1.6,
    )
    segs = outline_path(panel.outline)
    poly = flatten(segs)
    tree += _segments(segs, "Edge.Cuts", 0.05, ids)
    x0, x1 = min(p[0] for p in poly), max(p[0] for p in poly)

    for n, h in enumerate(panel.holes, 1):
        lib_id, fp = footprints.footprint_for(Hole(at=h.at, drill=h.drill, screw=h.screw))
        tree.append(_place_footprint(lib_id, fp, f"H{n}", h.at, ids))
        if h.countersink:
            tree.append(_gr_circle(h.at, h.countersink[0] / 2, "Dwgs.User", ids))

    if panel.usable_area:
        w, hh = panel.usable_area[0] / 2, panel.usable_area[1] / 2
        tree += _rect(-w, -hh, w, hh, "Dwgs.User", 0.1, ids)

    for label, y in enc.pcb_levels(panel):
        tree += _segments([Line((x0, y), (x1, y))], "Eco1.User", 0.1, ids)
        tree.append(_text(label, (x1 + 1, y), "Eco1.User", ids, size=0.8, justify_left=True))

    notes = [*panel.notes]
    if panel.holes:
        h = panel.holes[0]
        csk = f", countersink dia {h.countersink[0]:g} x {h.countersink[1]:g} deg" if h.countersink else ""
        notes.insert(0, f"Holes: dia {h.drill:g}{csk}" + (f", {h.screw}" if h.screw else ""))
    if panel.material or panel.thickness:
        notes.insert(0, f"Original panel: {panel.material or ''} {panel.thickness or ''} mm".strip())
    tree += _notes(notes, x0, max(p[1] for p in poly) + 5, ids)
    tree.append([Sym("embedded_fonts"), Sym("no")])
    return tree


def _gr_circle(c, r, layer, ids) -> list:
    return [Sym("gr_circle"), [Sym("center"), *_xy(c)], [Sym("end"), *_xy((c[0] + r, c[1]))],
            _stroke(0.1), [Sym("fill"), Sym("no")], [Sym("layer"), layer], ids()]


def _circle(c, r, n: int = 32):
    return circle_polygon(c[0], c[1], r, n)


def write(enc: Enclosure, mount: PcbMount, path) -> None:
    path.write_text(sexpr.dumps(board(enc, mount)) + "\n")


def write_panel(enc: Enclosure, panel: Panel, path) -> None:
    path.write_text(sexpr.dumps(panel_board(enc, panel)) + "\n")
