"""SVG previews at true scale (width/height in mm), printable as 1:1 drill templates."""

from __future__ import annotations

import math
from html import escape

from ..geometry import Arc, Line, bbox, flatten, keepout_polygon, outline_path
from ..schema import Enclosure, Panel, PcbMount

MARGIN = 6.0
STYLE = """
.outline{fill:#2f7d4f;fill-opacity:.12;stroke:#1f6b3f;stroke-width:.3}
.panel{fill:#8a8f98;fill-opacity:.15;stroke:#4a5260;stroke-width:.3}
.hole{fill:#fff;stroke:#c0392b;stroke-width:.25}
.cross{stroke:#c0392b;stroke-width:.12}
.boss{fill:none;stroke:#2c6fbb;stroke-width:.2;stroke-dasharray:.8 .5}
.csk{fill:none;stroke:#9b59b6;stroke-width:.2}
.keepout{fill:#e67e22;fill-opacity:.18;stroke:#e67e22;stroke-width:.15}
.wall{fill:none;stroke:#888;stroke-width:.2;stroke-dasharray:1.5 1}
.usable{fill:none;stroke:#16a0b5;stroke-width:.2;stroke-dasharray:1.5 1}
.level{stroke:#e67e22;stroke-width:.15}
text{font:1.6px sans-serif;fill:#333}
.small{font-size:1.1px}
"""


def _p(pt) -> str:
    return f"{pt[0]:.4f},{-pt[1]:.4f}"


def path_d(segs) -> str:
    d = [f"M{_p(segs[0].start)}"]
    for s in segs:
        if isinstance(s, Line):
            d.append(f"L{_p(s.end)}")
        else:
            a0 = math.atan2(s.start[1] - s.center[1], s.start[0] - s.center[0])
            a1 = math.atan2(s.end[1] - s.center[1], s.end[0] - s.center[0])
            sweep = (a1 - a0) % (2 * math.pi) if s.ccw else (a0 - a1) % (2 * math.pi)
            # Y is flipped, so a math-CCW arc runs counter-clockwise on screen (sweep flag 0).
            d.append(f"A{s.radius:.4f},{s.radius:.4f} 0 {int(sweep > math.pi)} {int(not s.ccw)} {_p(s.end)}")
    return " ".join(d) + " Z"


def _poly(pts, cls) -> str:
    return f'<polygon class="{cls}" points="{" ".join(_p(p) for p in pts)}"/>'


def _circle(c, r, cls) -> str:
    return f'<circle class="{cls}" cx="{c[0]:.4f}" cy="{-c[1]:.4f}" r="{r:.4f}"/>'


def _cross(c, r) -> str:
    x, y, k = c[0], -c[1], r * 1.6
    return (f'<line class="cross" x1="{x - k:.4f}" y1="{y:.4f}" x2="{x + k:.4f}" y2="{y:.4f}"/>'
            f'<line class="cross" x1="{x:.4f}" y1="{y - k:.4f}" x2="{x:.4f}" y2="{y + k:.4f}"/>')


def _doc(body: list[str], box, title: str) -> str:
    x0, y0, x1, y1 = box
    left, top = x0 - MARGIN, -(y1 + MARGIN)
    w, h = (x1 - x0) + 2 * MARGIN, (y1 - y0) + 2 * MARGIN + 4
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
            f'viewBox="{left:.3f} {top:.3f} {w:.3f} {h:.3f}">')
    caption = f'<text x="{left + 1:.3f}" y="{-y0 + MARGIN + 2.5:.3f}">{escape(title)} — print at 100% (1:1)</text>'
    return "\n".join([head, f"<title>{escape(title)}</title><style>{STYLE}</style>", *body, caption, "</svg>"]) + "\n"


def mount_svg(enc: Enclosure, mount: PcbMount) -> str:
    segs = outline_path(mount.outline)
    poly = flatten(segs)
    body = [f'<path class="outline" d="{path_d(segs)}"/>']
    boxes = [bbox(poly)]
    if enc.inner:
        w, h = enc.inner.length / 2, enc.inner.width / 2
        body.append(_poly([(-w, -h), (w, -h), (w, h), (-w, h)], "wall"))
        boxes.append((-w, -h, w, h))
    for k in mount.keepouts:
        body.append(_poly(keepout_polygon(k, poly), "keepout"))
    for hole in mount.all_holes():
        if hole.boss_diameter:
            body.append(_circle(hole.at, hole.boss_diameter / 2, "boss"))
        body.append(_circle(hole.at, hole.drill / 2, "hole"))
        body.append(_cross(hole.at, hole.drill / 2))
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return _doc(body, box, f"{enc.mfr.title()} {enc.part} — {mount.id}")


def panel_svg(enc: Enclosure, panel: Panel) -> str:
    segs = outline_path(panel.outline)
    poly = flatten(segs)
    x0, y0, x1, y1 = bbox(poly)
    body = [f'<path class="panel" d="{path_d(segs)}"/>']
    if panel.usable_area:
        w, h = panel.usable_area[0] / 2, panel.usable_area[1] / 2
        body.append(_poly([(-w, -h), (w, -h), (w, h), (-w, h)], "usable"))
    for label, y in enc.pcb_levels(panel):
        body.append(f'<line class="level" x1="{x0:.4f}" y1="{-y:.4f}" x2="{x1:.4f}" y2="{-y:.4f}"/>')
        body.append(f'<text class="small" x="{x1 + 0.8:.4f}" y="{-y + 0.4:.4f}">{escape(label)}</text>')
    for h in panel.holes:
        if h.countersink:
            body.append(_circle(h.at, h.countersink[0] / 2, "csk"))
        body.append(_circle(h.at, h.drill / 2, "hole"))
        body.append(_cross(h.at, h.drill / 2))
    extra = 10 if enc.pcb_levels(panel) else 0
    return _doc(body, (x0, y0, x1 + extra, y1), f"{enc.mfr.title()} {enc.part} — panel {panel.id}")
