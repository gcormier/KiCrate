"""Bud Industries scraper.

Bud publishes a PDF and DXF drawing per product but no max-PCB figure, and
most drawings show bosses/ribs that need judgement to turn into a board
outline. So this records what can be trusted (dimensions, drawing and CAD
links with hashes) and leaves `pcb_mounts` empty for a human to fill in
(see CONTRIBUTING.md; `kicrate extract` helps with the DXF).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re

from ..schema import Box3, Enclosure, Source
from .http import get

BASE = "https://www.budind.com"
IN = 25.4

# Series paths under /series/.
SERIES = [
    "general-use-boxes/extruded-aluminum-box-series",
    "nema-ip-rated-boxes/pn-series-nema-box",
    "general-use-boxes/utility-cabinet-series-cu",
]


def _text(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def series_products(series: str) -> list[str]:
    page = get(f"{BASE}/series/{series}/").decode("utf-8", "replace")
    return sorted(set(re.findall(r'href="(https://www\.budind\.com/product/[^"#?]+/)"', page)))


def _size(text: str, label: str) -> tuple[float, float, float] | None:
    m = re.search(label + r'[^:]*:?\s*:?\s*([\d.]+)"\s*x\s*([\d.]+)"\s*x\s*([\d.]+)"', text)
    return tuple(round(float(v) * IN, 3) for v in m.groups()) if m else None


def product(url: str) -> dict | None:
    page = get(url).decode("utf-8", "replace")
    text = _text(page)
    model = re.search(r"Model #\s*([A-Z0-9][A-Z0-9-]+)", text)
    outer = _size(text, r"External Size L x W x D")
    if not model or not outer:
        return None
    files = {}
    for path, ext in re.findall(r'href="(https://www\.budind\.com/wp-content/uploads/[^"]+\.(pdf|dxf))"', page):
        files.setdefault(ext, path)
    return {
        "pn": model.group(1),
        "url": url,
        "outer": outer,
        "inner": _size(text, r"Internal Size L x W x D"),
        "material": (re.search(r"Type of Plastic:\s*([A-Za-z ]+?)\s+Flammability", text) or [None, None])[1],
        "title": _text(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1)) if re.search(r"<h1", page) else None,
        "files": files,
    }


def scrape(series_list: list[str], limit: int | None = None, log=print) -> list[Enclosure]:
    today = dt.date.today()
    out: list[Enclosure] = []
    for series in series_list:
        urls = series_products(series)
        log(f"{series}: {len(urls)} products")
        groups: dict = {}
        for url in urls:
            try:
                p = product(url)
            except Exception as e:  # noqa: BLE001
                log(f"  {url}: fetch failed: {e}")
                continue
            if not p:
                log(f"  {url}: skipped (no model/size)")
                continue
            groups.setdefault((p["files"].get("dxf") or p["files"].get("pdf"), p["outer"]), []).append(p)
        for group in groups.values():
            if limit is not None and len(out) >= limit:
                return out
            rep = min(group, key=lambda p: (len(p["pn"]), p["pn"]))
            sources = [Source(url=rep["url"], kind="product_page", retrieved=today)]
            if "pdf" in rep["files"]:
                sources.append(Source(url=rep["files"]["pdf"], kind="drawing", retrieved=today))
            if "dxf" in rep["files"]:
                blob = get(rep["files"]["dxf"])
                sources.append(Source(url=rep["files"]["dxf"], kind="cad", sha256=hashlib.sha256(blob).hexdigest(), retrieved=today))
            o, i = rep["outer"], rep["inner"]
            enc = Enclosure(
                mfr="bud",
                part=rep["pn"],
                variants=sorted(p["pn"] for p in group if p is not rep),
                series=re.sub(r"-series.*", "", series.split("/")[-1]).upper(),
                description=rep["title"] if rep["title"] and rep["title"] != rep["pn"] else None,
                url=rep["url"],
                material=rep["material"],
                provenance="scraped",
                verified=False,
                last_checked=today,
                sources=sources,
                outer=Box3(length=o[0], width=o[1], height=o[2], note="Converted from inches."),
                inner=Box3(length=i[0], width=i[1], height=i[2], note="Converted from inches.") if i else None,
                notes=["PCB mounting geometry not entered yet: see the drawing/DXF (kicrate extract can help)."],
            )
            log(f"  {enc.part} (+{len(enc.variants)} variants)")
            out.append(enc)
    return out
