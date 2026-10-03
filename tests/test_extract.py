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
