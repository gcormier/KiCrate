"""Hammond Manufacturing scraper.

Series page -> part pages -> attributes (size, max PCB) + per-part DWG.
Colour variants (same size, max PCB and part-number stem) are folded
into one entry. Output is draft data (`provenance: scraped`, `verified: false`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import extract
from ..data import geometry_problems
from ..schema import Box3, Enclosure, PcbMount, RectOutline, Source
from .http import get

BASE = "https://www.hammfg.com"

# Series path under /electronics/small-case/ -> how PCBs mount.
SERIES = {
    "extruded/1455": "card_guide_slots",
    "plastic/1591xx": "bosses",
    "plastic/1593": "bosses",
    "plastic/1594": "floor",
}


@dataclass
class PartPage:
    pn: str
    attrs: dict[str, str]
    files: dict[str, str] = field(default_factory=dict)  # kind (pdf/dxf/stp) -> url

    @property
    def size_mm(self) -> tuple[float, ...] | None:
        m = re.search(r"\(([\d.]+) mm x ([\d.]+) mm(?: x ([\d.]+) mm)?\)", self.attrs.get("Size L x W x H", "") + self.attrs.get("Size L x W", ""))
        return tuple(float(v) for v in m.groups() if v) if m else None

    @property
    def max_pcb(self) -> tuple[float, float] | None:
        try:
            return float(self.attrs["Max. P.C. Board Length (mm)"]), float(self.attrs["Max. P.C. Board Width (mm)"])
        except (KeyError, ValueError):
            return None


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def series_parts(series: str) -> list[str]:
    page = get(f"{BASE}/electronics/small-case/{series}").decode("utf-8", "replace")
    return sorted(set(re.findall(r'href="/part/([A-Za-z0-9._-]+)\?referer', page)) - {"search"})


def part_page(pn: str) -> PartPage:
    page = get(f"{BASE}/part/{pn}").decode("utf-8", "replace")
    attrs = {}
    for th, td in re.findall(r"<tr>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>", page, re.S):
        attrs[_text(th)] = _text(td)
    files = {}
    for kind, path in re.findall(r'href="(?:https://www\.hammfg\.com)?/files/parts/(pdf|dxf|stp|x_t)/([^"?]+)', page):
        files.setdefault(kind, f"{BASE}/files/parts/{kind}/{path}")
    return PartPage(pn, attrs, files)


def _group_key(p: PartPage):
    # Colour suffixes (BK, BU, GY, TBU, ...) differ; size, PCB and the numeric stem do not.
    return (p.size_mm, p.max_pcb, re.sub(r"[A-Z]+$", "", p.pn))


def _dwg_from_zip(blob: bytes) -> tuple[bytes, str] | None:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.lower().endswith((".dwg", ".dxf")):
                return z.read(name), Path(name).suffix.lower()
    return None


def build_entry(series: str, pages: list[PartPage], today: dt.date) -> Enclosure:
    rep = min(pages, key=lambda p: (len(p.pn), p.pn))
    size, pcb = rep.size_mm, rep.max_pcb
    a = rep.attrs
    notes: list[str] = []
    sources = [Source(url=f"{BASE}/part/{rep.pn}", kind="product_page", retrieved=today)]
    ext = inner = None
    if "dxf" in rep.files:
        blob = get(rep.files["dxf"])
        sources.append(Source(url=rep.files["dxf"], kind="cad", sha256=hashlib.sha256(blob).hexdigest(), retrieved=today))
        cad = _dwg_from_zip(blob)
        if cad:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"drawing{cad[1]}"
                path.write_bytes(cad[0])
                try:
                    doc = extract.read_cad(path)
                    ext = extract.find_board(doc, pcb)
                    dims = extract.inside_dims(doc)
                    if size and {"length", "width", "height"} <= dims.keys():
                        inner = Box3(**{k: dims[k] for k in ("length", "width", "height")})
                    elif dims:
                        notes.append("Inside dims in drawing: " + ", ".join(f"{k} {v:g}" for k, v in sorted(dims.items())))
                except Exception as e:  # noqa: BLE001 - a bad drawing must not stop the run
                    notes.append(f"CAD extraction failed: {e}")

    from_pdf = False
    if "pdf" in rep.files:
        drawing = Source(url=rep.files["pdf"], kind="drawing", retrieved=today)
        if not ext:
            # Newer drawings put the "max recommended PCB" on a second PDF page that the DWG lacks.
            blob = get(rep.files["pdf"])
            drawing.sha256 = hashlib.sha256(blob).hexdigest()
            try:
                ext = extract.find_board_pdf(blob, pcb)
                from_pdf = ext is not None
            except Exception as e:  # noqa: BLE001
                notes.append(f"PDF extraction failed: {e}")
        sources.insert(1, drawing)

    if ext:
        outline, holes, pattern = ext.outline, ext.holes, ext.hole_pattern
        src = "PDF drawing" if from_pdf else "DWG"
        notes = [f"Outline and holes extracted from the manufacturer {src}.", *ext.notes, *notes]
    else:
        outline = RectOutline(size=(max(pcb), min(pcb)))
        holes, pattern = [], None
        notes = ["Outline is the max PCB size from Hammond part attributes; no PCB view found in the DWG or PDF. "
                 "Mounting holes, bosses and notches still need to be added.", *notes]

    mount = PcbMount(id="main", type=SERIES.get(series, "bosses"), outline=outline, holes=holes, hole_pattern=pattern, notes=notes)
    enc = Enclosure(
        mfr="hammond",
        part=rep.pn,
        variants=sorted(p.pn for p in pages if p is not rep),
        series=re.sub(r".*\((\w+) Series\).*", r"\1", a.get("Series", series.split("/")[-1])),
        description=a.get("Long Description") or a.get("Short Description"),
        url=f"{BASE}/part/{rep.pn}",
        material=a.get("Material"),
        provenance="scraped",
        verified=False,
        last_checked=today,
        sources=sources,
        outer=Box3(length=size[0], width=size[1], height=size[2] if len(size) > 2 else size[1]),
        inner=inner,
        pcb_mounts=[mount],
    )
    if inner and any("larger than inside" in p for p in geometry_problems(enc)):
        # Drawing labels sometimes refer to another level of the box; trust the PCB size.
        mount.notes.append(f"Drawing's inside dims {inner.length:g} x {inner.width:g} x {inner.height:g} conflict with the PCB size; omitted.")
        enc = enc.model_copy(update={"inner": None})
    return enc


def scrape(series_list: list[str], parts: list[str] | None = None, limit: int | None = None, log=print) -> list[Enclosure]:
    today = dt.date.today()
    out: list[Enclosure] = []
    for series in series_list:
        pns = parts or series_parts(series)
        log(f"{series}: {len(pns)} part links")
        pages = []
        for pn in pns:
            try:
                p = part_page(pn)
            except Exception as e:  # noqa: BLE001
                log(f"  {pn}: fetch failed: {e}")
                continue
            if not p.max_pcb or not p.size_mm:
                log(f"  {pn}: skipped (no size or max PCB attributes; probably an accessory)")
                continue
            pages.append(p)
        groups: dict = {}
        for p in pages:
            groups.setdefault(_group_key(p), []).append(p)
        for group in groups.values():
            if limit is not None and len(out) >= limit:
                return out
            try:
                enc = build_entry(series, group, today)
            except Exception as e:  # noqa: BLE001
                log(f"  {group[0].pn}: failed: {e}")
                continue
            log(f"  {enc.part} (+{len(enc.variants)} variants): "
                f"{'DWG outline' if 'extracted' in enc.pcb_mounts[0].notes[0] else 'max-PCB rectangle'}")
            out.append(enc)
    return out
