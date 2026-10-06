"""Extractor and scraper parsing, on synthetic drawings and HTML (no network)."""

import ezdxf
import pytest

from kicrate import extract
from kicrate.schema import PathOutline, RectOutline
from kicrate.scrapers import bud, hammond


def _drawing(tmp_path, scale=1.0, notch=True):
    """A 1591-style board (114.5 x 60.5, R3.5 corner notches, 4 holes) drawn at `scale`, offset."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ox, oy = 200.0, 120.0
    s = scale

    def P(x, y):
        return (ox + x * s, oy + y * s)

    if notch:
        lines = [((57.25, -22.25), (57.25, 22.25)), ((53.0, 22.25), (57.25, 22.25)), ((49.5, 25.75), (49.5, 30.25)),
                 ((49.5, 30.25), (-49.5, 30.25)), ((-49.5, 30.25), (-49.5, 25.75)), ((-53.0, 22.25), (-57.25, 22.25)),
                 ((-57.25, 22.25), (-57.25, -22.25)), ((-57.25, -22.25), (-53.0, -22.25)), ((-49.5, -25.75), (-49.5, -30.25)),
                 ((-49.5, -30.25), (49.5, -30.25)), ((49.5, -30.25), (49.5, -25.75)), ((53.0, -22.25), (57.25, -22.25))]
        for a, b in lines:
            msp.add_line(P(*a), P(*b))
        for (cx, cy), (a0, a1) in (((53.0, 25.75), (180, 270)), ((-53.0, 25.75), (270, 360)),
                                   ((-53.0, -25.75), (0, 90)), ((53.0, -25.75), (90, 180))):
            msp.add_arc(P(cx, cy), 3.5 * s, a0, a1)
    else:
        msp.add_lwpolyline([P(-57.25, -30.25), P(57.25, -30.25), P(57.25, 30.25), P(-57.25, 30.25)], close=True)
    for x in (-42.25, 42.25):
        for y in (-19.75, 19.75):
            msp.add_circle(P(x, y), 1.75 * s)
            msp.add_circle(P(x, y), 2.5 * s)  # a boss ring drawn concentric
    # Noise: a bigger rectangle that should not match.
    msp.add_lwpolyline([P(-70, -40), P(70, -40), P(70, 40), P(-70, 40)], close=True)
    if s != 1.0:
        d = msp.add_linear_dim(base=P(0, 40), p1=P(-57.25, -30.25), p2=P(57.25, -30.25),
                               override={"dimlfac": 1 / s})
        d.render()
    path = tmp_path / "d.dxf"
    doc.saveas(path)
    return extract.read_cad(path)


def test_finds_notched_outline_and_hole_pattern(tmp_path):
    ext = extract.find_board(_drawing(tmp_path), (60.5, 114.5))
    assert ext is not None
    assert isinstance(ext.outline, PathOutline)
    xs = [n[0] for n in ext.outline.nodes]
    ys = [n[1] for n in ext.outline.nodes]
    assert max(xs) - min(xs) == pytest.approx(114.5) and max(ys) - min(ys) == pytest.approx(60.5)
    assert ext.hole_pattern and ext.hole_pattern.pitch == pytest.approx((84.5, 39.5))
    assert ext.hole_pattern.drill == pytest.approx(3.5)  # smallest of the concentric circles


def test_scaled_view_and_rect(tmp_path):
    ext = extract.find_board(_drawing(tmp_path, scale=1.2, notch=False), (114.5, 60.5))
    assert ext is not None and ext.scale == pytest.approx(1 / 1.2)
    assert isinstance(ext.outline, RectOutline) and ext.outline.size == pytest.approx((114.5, 60.5))
    assert ext.hole_pattern.drill == pytest.approx(3.5)


def test_no_match(tmp_path):
    assert extract.find_board(_drawing(tmp_path), (80, 50)) is None


HAMMOND_PAGE = """
<table><tbody>
<tr><th>Part Number</th><td>1591XXCSBK</td></tr>
<tr><th>Series</th><td><a href="/x">ABS Plastic Multi-Purpose Enclosures w/ PC Board Standoffs (1591XX Series)</a></td></tr>
<tr><th>Long Description</th><td>enclosure - plastic</td></tr>
<tr><th>
  Size
  L x W x H
</th><td>4.7 in x 2.6 in x 1.4 in<br /><em>(119 mm x 66 mm x 36 mm)</em></td></tr>
<tr><th>Material</th><td>ABS Plastic</td></tr>
<tr><th>Max. P.C. Board Length (mm)</th><td>60.5</td></tr>
<tr><th>Max. P.C. Board Width (mm)</th><td>114.5</td></tr>
</tbody></table>
<a href="https://www.hammfg.com/files/parts/pdf/1591XXCSBK.pdf?v=1">pdf</a>
<a href="https://www.hammfg.com/files/parts/dxf/1591XXCSBK.zip?v=1">dwg</a>
"""


def test_hammond_part_page(monkeypatch):
    monkeypatch.setattr(hammond, "get", lambda url, **k: HAMMOND_PAGE.encode())
    p = hammond.part_page("1591XXCSBK")
    assert p.size_mm == (119.0, 66.0, 36.0)
    assert p.max_pcb == (60.5, 114.5)
    assert p.attrs["Material"] == "ABS Plastic"
    assert p.files["pdf"].endswith("/pdf/1591XXCSBK.pdf") and p.files["dxf"].endswith("/dxf/1591XXCSBK.zip")


BUD_PAGE = """<h1 class="x">EX-4521</h1><div>Model # EX-4521 Extruded Aluminum Box</div>
<a href="https://www.budind.com/wp-content/uploads/2019/01/hb4521.pdf">hb4521.pdf</a>
<a href="https://www.budind.com/wp-content/uploads/2019/01/hb4521.dxf">hb4521.dxf</a>
<p>External Size L x W x D: 6.14" x  3.18" x  5.38"</p><p>Internal Size L x W x D : 5.35" x  2.80" x  4.82"</p>"""


def test_bud_product(monkeypatch):
    monkeypatch.setattr(bud, "get", lambda url, **k: BUD_PAGE.encode())
    p = bud.product("https://www.budind.com/product/x/ex-4521/")
    assert p["pn"] == "EX-4521"
    assert p["outer"] == pytest.approx((155.956, 80.772, 136.652))
    assert p["inner"] == pytest.approx((135.89, 71.12, 122.428))
    assert p["files"]["dxf"].endswith("hb4521.dxf")


def test_save_draft_ignores_date_only_changes(tmp_path):
    import datetime as dt

    from kicrate import data
    from kicrate.schema import Box3, Enclosure, Source

    def enc(day, height=10.0):
        d = dt.date(2026, 10, day)
        return Enclosure(mfr="bud", part="X-1", series="X", provenance="scraped", last_checked=d,
                         sources=[Source(url="https://example.com/x", kind="product_page", retrieved=d)],
                         outer=Box3(length=30, width=20, height=height))

    assert data.save_draft(enc(3), tmp_path) == "new"
    before = (tmp_path / "bud" / "X-1.yaml").read_text()
    assert data.save_draft(enc(5), tmp_path) == "unchanged"
    assert (tmp_path / "bud" / "X-1.yaml").read_text() == before
    assert data.save_draft(enc(5, height=11.0), tmp_path) == "updated"
    assert "2026-10-05" in (tmp_path / "bud" / "X-1.yaml").read_text()


def _pdf_board(scale=1.0):
    """1593V-style PCB page: 98 x 48, R2 corners, 4 x dia 3.2 on 71.12 x 40.64; curves flattened like Hammond's PDFs."""
    import math

    import pymupdf

    k = 72 / 25.4 * scale  # mm -> pt
    ox, oy = 300, 300
    P = lambda x, y: pymupdf.Point(ox + x * k, oy - y * k)  # noqa: E731
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)
    pts = []
    for cx, cy, a0 in ((47, -22, -90), (47, 22, 0), (-47, 22, 90), (-47, -22, 180)):
        for i in range(9):  # flattened R2 corner
            a = math.radians(a0 + 90 * i / 8)
            pts.append(P(cx + 2 * math.cos(a), cy + 2 * math.sin(a)))
    page.draw_polyline(pts + [pts[0]])
    for x in (-35.56, 35.56):
        for y in (-20.32, 20.32):
            ring = [P(x + 1.6 * math.cos(2 * math.pi * i / 32), y + 1.6 * math.sin(2 * math.pi * i / 32)) for i in range(32)]
            page.draw_polyline(ring + [ring[0]])
    page.draw_circle(P(0, 0), 1.0 * k)  # a Bezier circle: also a hole
    page.draw_rect(pymupdf.Rect(20, 20, 772, 592))  # sheet border must not match
    return doc.tobytes()


@pytest.mark.parametrize("scale", [1.0, 0.6])
def test_pdf_board_flattened(scale):
    ext = extract.find_board_pdf(_pdf_board(scale), (98, 48))
    assert ext is not None
    xs = [n[0] for n in ext.outline.nodes]
    ys = [n[1] for n in ext.outline.nodes]
    assert max(xs) - min(xs) == pytest.approx(98, abs=0.05) and max(ys) - min(ys) == pytest.approx(48, abs=0.05)
    assert sum(len(n) == 4 for n in ext.outline.nodes) == 4  # corners rebuilt as arcs
    drills = sorted(h.drill for h in ext.holes)
    # 5 holes (4 + the Bezier one) -> no 4-hole pattern; check sizes and the corner ones' positions.
    assert drills == pytest.approx([2.0, 3.2, 3.2, 3.2, 3.2], abs=0.02)
    corners = sorted(h.at for h in ext.holes if h.drill > 3)
    assert corners[0] == pytest.approx((-35.56, -20.32), abs=0.05)


def _posts_dxf(tmp_path):
    """Bud-style top view (inches): outer wall, inner wall with a rib, 4 finned M3 posts + a callout."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(-1.25, -2.25), (1.25, -2.25), (1.25, 2.25), (-1.25, 2.25)], close=True)  # outer 2.5 x 4.5 in
    # inner wall 2.2 x 4.2 with a 0.1 x 0.1 rib on the right wall at y=0
    msp.add_lwpolyline([(-1.1, -2.1), (1.1, -2.1), (1.1, -0.05), (1.0, -0.05), (1.0, 0.05), (1.1, 0.05),
                        (1.1, 2.1), (-1.1, 2.1)], close=True)
    for x in (-0.55, 0.55):
        for y in (-1.85, 1.85):
            msp.add_circle((x, y), 0.156)  # boss
            msp.add_circle((x, y), 0.094)  # insert
    msp.add_circle((0, 1.0), 0.2)  # some other boss: an obstacle
    msp.add_mtext("0.312 DIA. BOSS WITH M3 x 0.188 LG. THREADED INSERT (4) PLACES").set_location((2.0, -3.0))
    msp.add_leader([(-0.55 - 0.156, -1.85), (-1.6, -3.0), (1.9, -3.0)])
    path = tmp_path / "posts.dxf"
    doc.saveas(path)
    return extract.read_cad(path)


@pytest.mark.parametrize("clearance", [2.0, 1.0])
def test_posts_board(tmp_path, clearance):
    from shapely.geometry import Point, Polygon

    found = extract.find_posts_boards(_posts_dxf(tmp_path), (114.3, 63.5), clearance=clearance)
    assert len(found) == 1
    mid, ext = found[0]
    assert ext.hole_pattern.pitch == pytest.approx((94.0, 27.94), abs=0.05)
    assert ext.hole_pattern.drill == 3.2 and ext.hole_pattern.screw == "M3"
    assert ext.hole_pattern.boss_diameter == pytest.approx(7.92, abs=0.02)
    board = Polygon(ext.outline.points, [c.points for c in ext.cutouts])
    xs = [p[0] for p in ext.outline.points]
    ys = [p[1] for p in ext.outline.points]
    # 4.2 x 2.2 in floor minus the clearance each side (long side on X).
    assert max(xs) - min(xs) == pytest.approx(4.2 * 25.4 - 2 * clearance, abs=0.15)
    assert max(ys) - min(ys) == pytest.approx(2.2 * 25.4 - 2 * clearance, abs=0.15)
    # The obstacle boss (r 5.08 mm; drawing (0, 1 in) -> board (-25.4, 0) mm) becomes a cut-out.
    assert len(ext.cutouts) == 1
    obstacle = Point(-25.4, 0).buffer(5.08)
    assert board.distance(obstacle) == pytest.approx(clearance, abs=0.15)
    # The rib (2.54 mm proud of the y-min wall after rotation) is cleared too.
    assert min(ys) > -(1.1 * 25.4) + clearance - 0.15


def test_posts_board_spline_view_hammond_callout(tmp_path):
    """Hammond 1593Q style: a mm drawing whose body view is splines (no line/arc loop), posts
    called out as "ACCEPTS #4 ... SCREW", board capped at a max PCB size centred on the posts."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_open_spline([(-56, -33), (56, -33), (56, 33), (-56, 33), (-56, -33)], degree=1)  # outer 112 x 66
    msp.add_open_spline([(-53, -30), (53, -30), (53, 30), (-53, 30), (-53, -30)], degree=1)  # floor 106 x 60
    for x in (-14, 30):
        for y in (-14, 14):
            msp.add_circle((x, y), 2.45)
            msp.add_circle((x, y), 1.2)
    msp.add_mtext("ACCEPTS #4 SELF-TAPPING SCREW (4) PLACES").set_location((40, -45))
    msp.add_leader([(30 - 2.45, -14), (30, -45), (40, -45)])
    path = tmp_path / "q.dxf"
    doc.saveas(path)
    found = extract.find_posts_boards(extract.read_cad(path), (112, 66), clearance=2.0,
                                      inner_size=(64, 50), cap_on_posts=True)
    assert len(found) == 1
    ext = found[0][1]
    assert ext.hole_pattern.drill == 3.2 and ext.hole_pattern.screw == "#4"
    assert ext.hole_pattern.pitch == pytest.approx((44, 28), abs=0.05)
    xs = [p[0] for p in ext.outline.points]
    ys = [p[1] for p in ext.outline.points]
    assert max(xs) - min(xs) == pytest.approx(60, abs=0.15) and max(ys) - min(ys) == pytest.approx(46, abs=0.15)
    # Centred on the posts, which sit off-centre in the box: equal margin each side of the holes.
    hp = ext.hole_pattern
    hx = (hp.center[0] - hp.pitch[0] / 2, hp.center[0] + hp.pitch[0] / 2)
    assert hx[0] - min(xs) == pytest.approx(max(xs) - hx[1], abs=0.15)


def test_posts_board_needs_callout(tmp_path):
    doc = _posts_dxf(tmp_path)
    for e in doc.modelspace().query("MTEXT"):
        e.text = "M4 x 0.315 LG. THREADED INSERT (4) PLACES"  # cover screws, not PCB posts
    assert extract.find_posts_boards(doc, (114.3, 63.5)) == []


def test_save_draft_keeps_known_variants(tmp_path):
    from kicrate import data
    from kicrate.schema import Box3, Enclosure

    def enc(variants):
        return Enclosure(mfr="bud", part="X-1", variants=variants, series="X", provenance="scraped",
                         outer=Box3(length=30, width=20, height=10))

    assert data.save_draft(enc(["X-1-C", "X-1-DG"]), tmp_path) == "new"
    # X-1-DG's page failed this run: it must not disappear.
    assert data.save_draft(enc(["X-1-C"]), tmp_path) == "unchanged"
    assert data.load_file(tmp_path / "bud" / "X-1.yaml").variants == ["X-1-C", "X-1-DG"]
    assert data.save_draft(enc(["X-1-C", "X-1-GY"]), tmp_path) == "updated"
    assert data.load_file(tmp_path / "bud" / "X-1.yaml").variants == ["X-1-C", "X-1-DG", "X-1-GY"]


def test_http_retries_transient_errors(tmp_path, monkeypatch):
    import io
    import urllib.error

    from kicrate.scrapers import http

    monkeypatch.setenv("KICRATE_CACHE", str(tmp_path))
    monkeypatch.setattr(http, "DELAY", 0)
    monkeypatch.setattr(http, "BACKOFF", 0)
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 504, "Gateway Timeout", {}, None)
        return Resp(b"ok")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    assert http.get("https://example.com/flaky") == b"ok"
    assert len(calls) == 3

    calls.clear()

    def not_found(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", not_found)
    with pytest.raises(urllib.error.HTTPError):
        http.get("https://example.com/missing")
    assert len(calls) == 1  # 4xx is not retried


def test_save_draft_keeps_existing_group_name(tmp_path):
    from kicrate import data
    from kicrate.schema import Box3, Enclosure

    def enc(part, variants):
        return Enclosure(mfr="bud", part=part, variants=variants, series="X", provenance="scraped",
                         outer=Box3(length=30, width=20, height=10))

    # First run: X-1's own page failed, so the group was saved under X-1-C.
    assert data.save_draft(enc("X-1-C", ["X-1-DG"]), tmp_path) == "new"
    # Later run sees X-1 again: it folds into the existing file instead of being skipped or duplicated.
    assert data.save_draft(enc("X-1", ["X-1-C", "X-1-DG"]), tmp_path) == "updated (into bud/X-1-C)"
    assert sorted(p.name for p in (tmp_path / "bud").iterdir()) == ["X-1-C.yaml"]
    assert data.load_file(tmp_path / "bud" / "X-1-C.yaml").variants == ["X-1", "X-1-DG"]
