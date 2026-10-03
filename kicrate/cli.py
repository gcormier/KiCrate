"""kicrate command line."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import data
from .gen import dxf, kicad_pcb, svg
from .schema import Enclosure

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "enclosure.schema.json"


def _load(args) -> list[Enclosure]:
    encs, errors = data.load_all(Path(args.data))
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        sys.exit(1)
    if getattr(args, "only", None):
        encs = [e for e in encs if e.part in args.only or any(v in args.only for v in e.variants)]
    return encs


def cmd_validate(args) -> None:
    encs = _load(args)
    print(f"ok: {len(encs)} enclosures")


def build_enclosure(enc: Enclosure, out: Path) -> dict:
    """Write every output for one enclosure; return its manifest entry."""
    d = out / enc.mfr / enc.part
    d.mkdir(parents=True, exist_ok=True)
    rel = lambda p: p.relative_to(out).as_posix()  # noqa: E731
    entry = {"key": enc.key, "mounts": [], "panels": []}
    for m in enc.pcb_mounts:
        stem = enc.part if len(enc.pcb_mounts) == 1 else f"{enc.part}_{m.id}"
        files = {"kicad_pcb": d / f"{stem}.kicad_pcb", "dxf": d / f"{stem}_edgecuts.dxf", "svg": d / f"{stem}.svg"}
        kicad_pcb.write(enc, m, files["kicad_pcb"])
        dxf.write(enc, m, files["dxf"])
        files["svg"].write_text(svg.mount_svg(enc, m))
        entry["mounts"].append({"id": m.id, "type": m.type, **{k: rel(v) for k, v in files.items()}})
    for p in enc.panels:
        stem = f"{enc.part}_panel_{p.id}"
        files = {"kicad_pcb": d / f"{stem}.kicad_pcb", "dxf": d / f"{stem}.dxf", "svg": d / f"{stem}.svg"}
        kicad_pcb.write_panel(enc, p, files["kicad_pcb"])
        dxf.write_panel(enc, p, files["dxf"])
        files["svg"].write_text(svg.panel_svg(enc, p))
        entry["panels"].append({"id": p.id, "faces": p.faces, **{k: rel(v) for k, v in files.items()}})
    meta = d / f"{enc.part}.json"
    meta.write_text(enc.model_dump_json(indent=2, exclude_none=True))
    entry["json"] = rel(meta)
    return entry


def cmd_build(args) -> None:
    out = Path(args.out)
    manifest = []
    for enc in _load(args):
        entry = build_enclosure(enc, out)
        manifest.append(entry)
        print(f"{enc.key}: {len(entry['mounts'])} board(s), {len(entry['panels'])} panel(s)")
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def cmd_drc(args) -> None:
    """Load every generated board in KiCad and run DRC (needs kicad-cli)."""
    cli = shutil.which("kicad-cli")
    if not cli:
        sys.exit("kicad-cli not found")
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for pcb in sorted(Path(args.out).rglob("*.kicad_pcb")):
            report = Path(tmp) / f"{pcb.stem}.json"
            r = subprocess.run(
                [cli, "pcb", "drc", "--format", "json", "--severity-error", "--exit-code-violations", "-o", str(report), str(pcb)],
                capture_output=True, text=True,
            )
            pcb.with_suffix(".kicad_prl").unlink(missing_ok=True)  # KiCad writes local settings next to the board
            if r.returncode == 0:
                print(f"ok   {pcb}")
                continue
            failed += 1
            print(f"FAIL {pcb}\n{r.stdout}{r.stderr}")
            if report.exists():
                rep = json.loads(report.read_text())
                for v in rep.get("violations", []) + rep.get("unconnected_items", []):
                    print(f"  - {v.get('type')}: {v.get('description')}")
    sys.exit(1 if failed else 0)


def cmd_schema(args) -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(Enclosure.model_json_schema(), indent=2) + "\n")
    print(SCHEMA_PATH)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="kicrate")
    p.add_argument("--data", default=str(data.DATA_DIR), help="data directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="validate all YAML").set_defaults(fn=cmd_validate)
    b = sub.add_parser("build", help="generate DXF and KiCad files")
    b.add_argument("--out", default="build")
    b.add_argument("--only", nargs="*", help="part numbers to build")
    b.set_defaults(fn=cmd_build)
    d = sub.add_parser("drc", help="run KiCad DRC on generated boards")
    d.add_argument("--out", default="build")
    d.set_defaults(fn=cmd_drc)
    sub.add_parser("schema", help="write JSON Schema for editors").set_defaults(fn=cmd_schema)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
