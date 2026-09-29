import math

import pytest

from kicrate.geometry import Arc, Line, flatten, outline_path, point_in_polygon
from kicrate.schema import Notches, PolygonOutline, RectOutline


def _closed(segs):
    for a, b in zip(segs, segs[1:] + segs[:1]):
        assert math.dist(a.end, b.start) < 1e-9


def _area(poly):
    return sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly))) / 2


def test_plain_rect():
    segs = outline_path(RectOutline(size=(10, 4)))
    assert len(segs) == 4 and all(isinstance(s, Line) for s in segs)
    _closed(segs)
    assert _area(flatten(segs)) == pytest.approx(40)


def test_rounded_rect_area_and_arcs():
    r = 1.5
    segs = outline_path(RectOutline(size=(20, 10), corner_radius=r))
    _closed(segs)
    arcs = [s for s in segs if isinstance(s, Arc)]
    assert len(arcs) == 4 and all(a.ccw for a in arcs)
    expected = 200 - (4 - math.pi) * r * r
    assert _area(flatten(segs, step_deg=0.5)) == pytest.approx(expected, rel=1e-4)


def test_corner_notches():
    o = RectOutline(size=(114.5, 60.5), corner_notches=Notches(size=(7.75, 8.0), outer_radius=3.5))
    segs = outline_path(o)
    _closed(segs)
    poly = flatten(segs, step_deg=0.5)
    # Area: rect minus four notches, plus the material the R3.5 fillets remove.
    expected = 114.5 * 60.5 - 4 * 7.75 * 8.0 - 4 * (1 - math.pi / 4) * 3.5**2
    assert _area(poly) == pytest.approx(expected, rel=1e-4)
    assert not point_in_polygon((57.0, 30.0), poly)  # inside a notch
    assert point_in_polygon((57.0, 0.0), poly)
    assert point_in_polygon((0.0, 30.0), poly)
    # Fillets are on convex corners (CCW) and top edge spans 99.0 between notches.
    ys = [p for p in poly if abs(p[1] - 30.25) < 1e-9]
    assert max(p[0] for p in ys) - min(p[0] for p in ys) == pytest.approx(99.0 - 7.0)


def test_concave_fillet_is_clockwise():
    o = RectOutline(size=(20, 20), corner_notches=Notches(size=(5, 5), inner_radius=1))
    arcs = [s for s in outline_path(o) if isinstance(s, Arc)]
    assert len(arcs) == 4 and not any(a.ccw for a in arcs)


def test_polygon_with_radius():
    o = PolygonOutline(points=[(0, 0), (10, 0), (10, 10, 2), (0, 10)])
    segs = outline_path(o)
    _closed(segs)
    assert sum(isinstance(s, Arc) for s in segs) == 1


def test_radius_too_large():
    with pytest.raises(ValueError):
        outline_path(RectOutline(size=(4, 4), corner_radius=3))
