"""kicrate command line."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import data
from .gen import dxf, kicad_pcb
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


def cmd_build(args) -> None:
    out = Path(args.out)
    built = []
    for enc in _load(args):
        d = out / enc.mfr / enc.part
        d.mkdir(parents=True, exist_ok=True)
        for m in enc.pcb_mounts:
            stem = enc.part if len(enc.pcb_mounts) == 1 else f"{enc.part}_{m.id}"
            kicad_pcb.write(enc, m, d / f"{stem}.kicad_pcb")
            dxf.write(enc, m, d / f"{stem}_edgecuts.dxf")
            built.append(str(d / stem))
        (d / f"{enc.part}.json").write_text(enc.model_dump_json(indent=2))
    for b in built:
        print(b)


def cmd_drc(args) -> None:
    """Load every generated board in KiCad and run DRC (needs kicad-cli)."""
    cli = shutil.which("kicad-cli")
    if not cli:
        sys.exit("kicad-cli not found")
    failed = 0
    for pcb in sorted(Path(args.out).rglob("*.kicad_pcb")):
        report = pcb.with_suffix(".drc.json")
        r = subprocess.run(
            [cli, "pcb", "drc", "--format", "json", "--severity-error", "--exit-code-violations", "-o", str(report), str(pcb)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            failed += 1
            print(f"FAIL {pcb}\n{r.stdout}{r.stderr}")
            if report.exists():
                rep = json.loads(report.read_text())
                for v in rep.get("violations", []) + rep.get("unconnected_items", []):
                    print(f"  - {v.get('type')}: {v.get('description')}")
        else:
            print(f"ok   {pcb}")
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
