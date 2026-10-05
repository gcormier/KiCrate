"""Load and sanity-check enclosure YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .geometry import bbox, distance_to_polygon, flatten, keepout_polygon, outline_path, point_in_polygon
from .schema import Enclosure

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class DataError(Exception):
    pass


def load_file(path: Path) -> Enclosure:
    try:
        enc = Enclosure.model_validate(yaml.safe_load(path.read_text()))
    except ValidationError as e:
        raise DataError(f"{path}: {e}") from e
    expected = Path(enc.mfr) / f"{enc.part}.yaml"
    if path.parts[-2:] != expected.parts:
        raise DataError(f"{path}: must live at data/{expected} (mfr/part)")
    return enc


def iter_files(root: Path = DATA_DIR) -> list[Path]:
    return sorted(root.glob("*/*.yaml"))


def geometry_problems(enc: Enclosure) -> list[str]:
    problems = []
    for m in enc.pcb_mounts:
        where = f"{enc.key}/{m.id}"
        try:
            poly = flatten(outline_path(m.outline))
        except ValueError as e:
            problems.append(f"{where}: outline: {e}")
            continue
        cut_polys = [flatten(outline_path(c)) for c in m.cutouts]
        for c in cut_polys:
            if not all(point_in_polygon(p, poly) for p in c):
                problems.append(f"{where}: a cut-out crosses the board edge")
        for h in m.all_holes():
            if not point_in_polygon(h.at, poly) or any(point_in_polygon(h.at, c) for c in cut_polys):
                problems.append(f"{where}: hole at {h.at} is outside the board")
            elif distance_to_polygon(h.at, poly) < h.drill / 2:
                problems.append(f"{where}: hole at {h.at} breaks the board edge")
        for k in m.keepouts:
            x0, y0, x1, y1 = bbox(keepout_polygon(k, poly))
            bx0, by0, bx1, by1 = bbox(poly)
            if x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1:
                problems.append(f"{where}: keepout {k.reason or ''} lies entirely off the board")
        if enc.inner:
            x0, y0, x1, y1 = bbox(poly)
            if x1 - x0 > enc.inner.length + 1e-6 or y1 - y0 > enc.inner.width + 1e-6:
                problems.append(f"{where}: board {x1 - x0:g} x {y1 - y0:g} is larger than inside {enc.inner.length:g} x {enc.inner.width:g}")
    for p in enc.panels:
        try:
            poly = flatten(outline_path(p.outline))
        except ValueError as e:
            problems.append(f"{enc.key}/panel {p.id}: outline: {e}")
            continue
        for h in p.holes:
            if not point_in_polygon(h.at, poly):
                problems.append(f"{enc.key}/panel {p.id}: hole at {h.at} is outside the panel")
    return problems


def load_all(root: Path = DATA_DIR) -> tuple[list[Enclosure], list[str]]:
    encs, errors = [], []
    seen: dict[str, Path] = {}
    for f in iter_files(root):
        try:
            enc = load_file(f)
        except DataError as e:
            errors.append(str(e))
            continue
        for pn in [enc.part, *enc.variants]:
            if pn in seen:
                errors.append(f"{f}: part number {pn} also in {seen[pn]}")
            seen[pn] = f
        errors += geometry_problems(enc)
        encs.append(enc)
    return encs, errors


HEADER = "# yaml-language-server: $schema=../../schema/enclosure.schema.json\n"


def _prune(v):
    if isinstance(v, dict):
        return {k: _prune(x) for k, x in v.items() if x is not None and x != [] and x != {}}
    if isinstance(v, list):
        return [_prune(x) for x in v]
    return v


class _Dumper(yaml.SafeDumper):
    pass


def _list(d, data):
    # Short lists of numbers inline ([1, 2]); everything else block style.
    flow = len(data) <= 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in data)
    return d.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(list, _list)


def yaml_text(body) -> str:
    return yaml.dump(_prune(body), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def dump_yaml(enc: Enclosure) -> str:
    body = _prune(enc.model_dump(mode="json", exclude={"schema_version"}))
    body["verified"] = enc.verified  # always explicit
    return HEADER + yaml_text(body)


def _undated(enc: Enclosure) -> dict:
    d = enc.model_dump(mode="json", exclude={"last_checked"})
    for src in d["sources"]:
        src.pop("retrieved", None)
    return d


def save_draft(enc: Enclosure, root: Path = DATA_DIR) -> str:
    """Write scraped data unless a human-made or verified entry already covers the part.

    Returns "new", "updated" or "skipped: <reason>".
    """
    existing, _ = load_all(root)
    pns = {enc.part, *enc.variants}
    for e in existing:
        if pns & {e.part, *e.variants} and e.part != enc.part:
            return f"skipped: covered by {e.key}"
    path = root / enc.mfr / f"{enc.part}.yaml"
    status = "new"
    if path.exists():
        old = load_file(path)
        if old.verified or old.provenance != "scraped":
            return f"skipped: {old.provenance}{' verified' if old.verified else ''} entry exists"
        status = "updated"
    problems = geometry_problems(enc)
    if problems:
        return "skipped: " + "; ".join(problems)
    if path.exists() and _undated(load_file(path)) == _undated(enc):
        return "unchanged"  # only the check/retrieval dates would move; avoid churn
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump_yaml(enc)
    path.write_text(text)
    return status
