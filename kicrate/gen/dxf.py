"""DXF (R2010, millimetres) for a PCB mount: outline, holes, keepouts, enclosure wall."""

from __future__ import annotations

import math

import ezdxf
from ezdxf import units

from ..geometry import Arc, Line, flatten, keepout_polygon, outline_path
from ..schema import Enclosure, Panel, PcbMount

LAYERS = {
    "OUTLINE": 7,  # white/black
    "HOLES": 1,  # red
    "KEEPOUT_TOP": 3,  # green
    "KEEPOUT_BOTTOM": 5,  # blue
    "ENCLOSURE": 8,  # grey
    "NOTES": 2,  # yellow
    "COUNTERSINK": 6,  # magenta
    "USABLE_AREA": 4,  # cyan
    "PCB_LEVELS": 30,  # orange
}


def _add_segments(msp, segs, layer: str) -> None:
    for s in segs:
        if isinstance(s, Line):
            msp.add_line(s.start, s.end, dxfattribs={"layer": layer})
        elif isinstance(s, Arc):
            a0 = math.degrees(math.atan2(s.start[1] - s.center[1], s.start[0] - s.center[0]))
            a1 = math.degrees(math.atan2(s.end[1] - s.center[1], s.end[0] - s.center[0]))
            if not s.ccw:  # DXF arcs are always counter-clockwise
                a0, a1 = a1, a0
            msp.add_arc(s.center, s.radius, a0, a1, dxfattribs={"layer": layer})


def _new():
    doc = ezdxf.new("R2010", setup=True)
    doc.units = units.MM
    doc.header["$MEASUREMENT"] = 1
    for name, color in LAYERS.items():
        doc.layers.add(name, color=color)
    return doc, doc.modelspace()


def document(enc: Enclosure, mount: PcbMount) -> ezdxf.document.Drawing:
    doc, msp = _new()

    segs = outline_path(mount.outline)
    _add_segments(msp, segs, "OUTLINE")
    board_poly = flatten(segs)
    for c in mount.cutouts:
        _add_segments(msp, outline_path(c), "OUTLINE")

    for h in mount.all_holes():
        msp.add_circle(h.at, h.drill / 2, dxfattribs={"layer": "HOLES"})
        if h.boss_diameter:
            msp.add_circle(h.at, h.boss_diameter / 2, dxfattribs={"layer": "KEEPOUT_BOTTOM"})
        if h.head_diameter:
            msp.add_circle(h.at, h.head_diameter / 2, dxfattribs={"layer": "KEEPOUT_TOP"})

    for k in mount.keepouts:
        poly = keepout_polygon(k, board_poly)
        for side in (["top", "bottom"] if k.side == "both" else [k.side]):
            msp.add_lwpolyline(poly, close=True, dxfattribs={"layer": f"KEEPOUT_{side.upper()}"})

    if enc.inner:
        w, h = enc.inner.length / 2, enc.inner.width / 2
        msp.add_lwpolyline([(-w, -h), (w, -h), (w, h), (-w, h)], close=True, dxfattribs={"layer": "ENCLOSURE"})

    top = max(p[1] for p in board_poly)
    if enc.inner:
        top = max(top, enc.inner.width / 2)
    label = f"{enc.mfr.title()} {enc.part} / {mount.id} ({'verified' if enc.verified else 'UNVERIFIED'})"
    msp.add_text(label, height=2.0, dxfattribs={"layer": "NOTES"}).set_placement((min(p[0] for p in board_poly), top + 4))
    return doc


def panel_document(enc: Enclosure, panel: Panel) -> ezdxf.document.Drawing:
    doc, msp = _new()
    segs = outline_path(panel.outline)
    _add_segments(msp, segs, "OUTLINE")
    poly = flatten(segs)
    for h in panel.holes:
        msp.add_circle(h.at, h.drill / 2, dxfattribs={"layer": "HOLES"})
        if h.countersink:
            msp.add_circle(h.at, h.countersink[0] / 2, dxfattribs={"layer": "COUNTERSINK"})
    if panel.usable_area:
        w, hh = panel.usable_area[0] / 2, panel.usable_area[1] / 2
        msp.add_lwpolyline([(-w, -hh), (w, -hh), (w, hh), (-w, hh)], close=True, dxfattribs={"layer": "USABLE_AREA"})
    x0, x1 = min(p[0] for p in poly), max(p[0] for p in poly)
    for label, y in enc.pcb_levels(panel):
        msp.add_line((x0, y), (x1, y), dxfattribs={"layer": "PCB_LEVELS"})
        msp.add_text(label, height=0.8, dxfattribs={"layer": "PCB_LEVELS"}).set_placement((x1 + 1, y))
    label = f"{enc.mfr.title()} {enc.part} / panel {panel.id} ({'verified' if enc.verified else 'UNVERIFIED'})"
    msp.add_text(label, height=2.0, dxfattribs={"layer": "NOTES"}).set_placement((x0, max(p[1] for p in poly) + 4))
    return doc


def write(enc: Enclosure, mount: PcbMount, path) -> None:
    document(enc, mount).saveas(path)


def write_panel(enc: Enclosure, panel: Panel, path) -> None:
    panel_document(enc, panel).saveas(path)
