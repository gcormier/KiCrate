import shutil
import subprocess

import ezdxf
import pytest

from kicrate import data, footprints, sexpr
from kicrate.gen import dxf, kicad_pcb
from kicrate.schema import Hole

ENCS, ERRORS = data.load_all()
MOUNTS = [(e, m) for e in ENCS for m in e.pcb_mounts]
IDS = [f"{e.part}-{m.id}" for e, m in MOUNTS]


def test_data_valid():
    assert not ERRORS, "\n".join(ERRORS)
    assert ENCS


def test_sexpr_roundtrip():
    src = '(a "x \\"q\\"" (b 1 -2.5) (c yes) (d "") 1e-3)'
    tree = sexpr.loads(src)
    assert tree[1] == 'x "q"' and tree[2] == ["b", 1, -2.5] and tree[-1] == 0.001
    assert sexpr.loads(sexpr.dumps(tree)) == tree


def test_footprint_pick_exact_drill():
    if not footprints.library_dir():
        pytest.skip("no local KiCad footprint library")
    assert footprints.pick(Hole(at=(0, 0), drill=3.2, screw="M3")) == "MountingHole:MountingHole_3.2mm_M3"
    assert footprints.pick(Hole(at=(0, 0), drill=3.5)) == "MountingHole:MountingHole_3.5mm"
    assert footprints.pick(Hole(at=(0, 0), drill=3.33)).startswith("KiCrate:")


@pytest.mark.parametrize("enc,mount", MOUNTS, ids=IDS)
def test_board_structure(enc, mount):
    tree = sexpr.loads(sexpr.dumps(kicad_pcb.board(enc, mount)))
    assert tree[0] == "kicad_pcb"
    fps = sexpr.find_all(tree, "footprint")
    assert len(fps) == len(mount.all_holes())
    edges = [n for n in tree if isinstance(n, list) and n[0] in ("gr_line", "gr_arc") and sexpr.find(n, "layer")[1] == "Edge.Cuts"]
    assert edges


@pytest.mark.parametrize("enc,mount", MOUNTS, ids=IDS)
def test_dxf(enc, mount, tmp_path):
    p = tmp_path / "x.dxf"
    dxf.write(enc, mount, p)
    doc = ezdxf.readfile(p)
    msp = doc.modelspace()
    assert len(msp.query("CIRCLE[layer=='HOLES']")) == len(mount.all_holes())
    assert len(msp.query("LINE ARC")) > 0


@pytest.mark.skipif(not shutil.which("kicad-cli"), reason="kicad-cli not installed")
@pytest.mark.parametrize("enc,mount", MOUNTS, ids=IDS)
def test_kicad_loads_and_passes_drc(enc, mount, tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    kicad_pcb.write(enc, mount, pcb)
    r = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--severity-error", "--exit-code-violations", "-o", str(tmp_path / "drc.rpt"), str(pcb)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr + (tmp_path / "drc.rpt").read_text()


PANELS = [(e, p) for e in ENCS for p in e.panels]
PANEL_IDS = [f"{e.part}-{p.id}" for e, p in PANELS]


@pytest.mark.parametrize("enc,panel", PANELS, ids=PANEL_IDS)
def test_panel_outputs(enc, panel, tmp_path):
    tree = sexpr.loads(sexpr.dumps(kicad_pcb.panel_board(enc, panel)))
    assert len(sexpr.find_all(tree, "footprint")) == len(panel.holes)
    p = tmp_path / "p.dxf"
    dxf.write_panel(enc, panel, p)
    msp = ezdxf.readfile(p).modelspace()
    assert len(msp.query("CIRCLE[layer=='HOLES']")) == len(panel.holes)
    assert len(msp.query("LINE[layer=='PCB_LEVELS']")) == len(enc.pcb_levels(panel))


@pytest.mark.parametrize("enc,mount", MOUNTS, ids=IDS)
def test_svg_is_true_scale(enc, mount):
    import re
    from kicrate.gen import svg

    doc = svg.mount_svg(enc, mount)
    w = float(re.search(r'width="([\d.]+)mm"', doc).group(1))
    vb_w = float(re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+)', doc).group(1))
    assert w == pytest.approx(vb_w, abs=0.01)  # 1 user unit == 1 mm


@pytest.mark.skipif(not shutil.which("kicad-cli"), reason="kicad-cli not installed")
@pytest.mark.parametrize("enc,panel", PANELS, ids=PANEL_IDS)
def test_kicad_loads_panel(enc, panel, tmp_path):
    pcb = tmp_path / "p.kicad_pcb"
    kicad_pcb.write_panel(enc, panel, pcb)
    r = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--severity-error", "--exit-code-violations", "-o", str(tmp_path / "drc.rpt"), str(pcb)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr + (tmp_path / "drc.rpt").read_text()
