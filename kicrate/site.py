"""Static gallery (GitHub Pages) over a build directory."""

from __future__ import annotations

import json
import shutil
import zipfile
from html import escape
from pathlib import Path

from . import __version__
from .schema import Enclosure

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d2327;--muted:#5d6670;--line:#e2e4e7;--accent:#1f6b3f;--warn:#a15c00;--warnbg:#fff3e0}
@media (prefers-color-scheme:dark){:root{--bg:#15181b;--card:#1e2226;--ink:#e6e8ea;--muted:#9aa3ad;--line:#30363d;--accent:#5cc28a;--warn:#ffb74d;--warnbg:#3a2a12}
 .card img{background:#fff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:28px 16px 12px;max-width:1200px;margin:auto}h1{margin:0 0 4px;font-size:26px}header p{margin:0;color:var(--muted)}
.controls{max-width:1200px;margin:auto;padding:8px 16px;display:flex;gap:8px;flex-wrap:wrap}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
input{flex:1;min-width:200px}
main{max-width:1200px;margin:auto;padding:8px 16px 40px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:8px}
.card img{width:100%;height:170px;object-fit:contain;border-radius:8px;border:1px solid var(--line)}
.card h2{font-size:17px;margin:0}.meta{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);margin-right:4px}
.badge.unverified{color:var(--warn);background:var(--warnbg);border-color:transparent}.badge.verified{color:var(--accent)}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0;font-size:13px}dt{color:var(--muted)}dd{margin:0}
.files{display:flex;flex-wrap:wrap;gap:6px;font-size:13px}.files a{color:var(--accent);text-decoration:none;border:1px solid var(--line);padding:2px 8px;border-radius:6px}
.files a:hover{border-color:var(--accent)}
.todo{height:170px;display:flex;align-items:center;justify-content:center;text-align:center;padding:16px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);font-size:13px}.group{width:100%;font-size:12px;color:var(--muted);margin-top:2px}
footer{max-width:1200px;margin:auto;padding:0 16px 30px;color:var(--muted);font-size:13px}footer a{color:inherit}
"""

JS = """
const q=document.getElementById('q'),m=document.getElementById('mfr'),v=document.getElementById('ver'),cards=[...document.querySelectorAll('.card')];
function f(){const t=q.value.toLowerCase().trim();let n=0;for(const c of cards){const ok=(!t||c.dataset.s.includes(t))&&(!m.value||c.dataset.m===m.value)&&(!v.value||c.dataset.v===v.value);c.hidden=!ok;n+=ok}document.getElementById('n').textContent=n}
for(const e of [q,m,v])e.addEventListener('input',f);f();
"""


def _dims(b) -> str:
    return f"{b['length']:g} × {b['width']:g} × {b['height']:g} mm"


def _outline_size(outline: dict) -> str:
    if outline.get("size"):
        return f"{outline['size'][0]:g} × {outline['size'][1]:g} mm"
    pts = [n[:2] for n in outline.get("nodes") or outline.get("points") or []]
    if not pts:
        return "?"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return f"{max(xs) - min(xs):g} × {max(ys) - min(ys):g} mm"


def _card(entry: dict, enc: dict) -> str:
    links = []
    for m in entry["mounts"]:
        tag = "" if len(entry["mounts"]) == 1 else f" {m['id']}"
        links += [f'<a href="{m["kicad_pcb"]}" download>KiCad{tag}</a>', f'<a href="{m["dxf"]}" download>DXF{tag}</a>',
                  f'<a href="{m["svg"]}">SVG 1:1{tag}</a>']
    for p in entry["panels"]:
        links += [f'<a href="{p["kicad_pcb"]}" download>Panel {p["id"]} KiCad</a>', f'<a href="{p["dxf"]}" download>Panel {p["id"]} DXF</a>',
                  f'<a href="{p["svg"]}">Panel {p["id"]} SVG</a>']
    links += [f'<a href="{entry["json"]}">JSON</a>', f'<a href="{entry["zip"]}" download>All (zip)</a>']
    for src in enc.get("sources", []):
        if src["kind"] in ("drawing", "cad"):
            links.append(f'<a href="{escape(src["url"])}" rel="noopener">{"Drawing" if src["kind"] == "drawing" else "Mfr CAD"} ↗</a>')
    preview = entry["mounts"][0]["svg"] if entry["mounts"] else (entry["panels"][0]["svg"] if entry["panels"] else "")
    img = (f'<img src="{preview}" alt="{escape(enc["part"])} outline" loading="lazy">' if preview
           else '<div class="todo">PCB layout not entered yet — help by adding it from the drawing</div>')
    verified = "verified" if enc.get("verified") else "unverified"
    rows = [("Outside", _dims(enc["outer"]))]
    if enc.get("inner"):
        rows.append(("Inside", _dims(enc["inner"])))
    for m in enc.get("pcb_mounts", []):
        rows.append(("PCB" if len(enc["pcb_mounts"]) == 1 else f"PCB {m['id']}", f"{_outline_size(m['outline'])} · {m['type'].replace('_', ' ')}"))
    if enc.get("panels"):
        rows.append(("Panels", ", ".join(f"{p['id']} ({'/'.join(p['faces'])})" for p in enc["panels"])))
    search = " ".join([enc["part"], *enc.get("variants", []), enc["series"], enc["mfr"], enc.get("description") or "",
                       "" if enc.get("pcb_mounts") else "todo needs pcb"]).lower()
    variants = f'<div class="group">Also: {escape(", ".join(enc["variants"]))}</div>' if enc.get("variants") else ""
    return f"""<article class="card" data-s="{escape(search)}" data-m="{escape(enc['mfr'])}" data-v="{verified}">
{img}
<h2>{escape(enc['part'])}</h2>
<div class="meta"><span class="badge {verified}">{verified}</span><span class="badge">{escape(enc['provenance'])}</span>{escape(enc['mfr'].title())} · {escape(enc['series'])}</div>
<div class="meta">{escape(enc.get('description') or '')}</div>
<dl>{''.join(f'<dt>{escape(k)}</dt><dd>{escape(v)}</dd>' for k, v in rows)}</dl>
<div class="files">{''.join(links)}</div>{variants}
</article>"""


def build_site(build: Path, out: Path, repo_url: str = "https://github.com/gcormier/KiCrate") -> Path:
    manifest = json.loads((build / "manifest.json").read_text())
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(build, out)
    cards, mfrs = [], set()
    for entry in sorted(manifest, key=lambda e: e["key"]):
        enc = json.loads((build / entry["json"]).read_text())
        Enclosure.model_validate(enc)  # build output must still be valid data
        part_dir = (out / entry["json"]).parent
        zpath = part_dir / f"{enc['part']}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(part_dir.iterdir()):
                if f != zpath:
                    z.write(f, f.name)
        entry["zip"] = zpath.relative_to(out).as_posix()
        mfrs.add(enc["mfr"])
        cards.append(_card(entry, enc))
    options = "".join(f'<option value="{m}">{m.title()}</option>' for m in sorted(mfrs))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KiCrate enclosures</title><meta name="description" content="PCB outlines, KiCad boards and panel files for off-the-shelf enclosures">
<style>{CSS}</style></head><body>
<header><h1>KiCrate</h1><p>PCB outlines, KiCad 10 boards and panel files for off-the-shelf enclosures. <span id="n"></span> shown.</p></header>
<div class="controls"><input id="q" type="search" placeholder="Search part, series, description…" aria-label="Search">
<select id="mfr" aria-label="Manufacturer"><option value="">All manufacturers</option>{options}</select>
<select id="ver" aria-label="Status"><option value="">Any status</option><option value="verified">Verified</option><option value="unverified">Unverified</option></select></div>
<main>{''.join(cards)}</main>
<footer>Unverified entries are drafts from manufacturer drawings: check against a real enclosure before ordering boards.
Generated by KiCrate {__version__} · <a href="{repo_url}">source &amp; data</a> · <a href="manifest.json">manifest.json</a></footer>
<script>{JS}</script></body></html>
"""
    (out / "index.html").write_text(page)
    (out / ".nojekyll").write_text("")
    return out / "index.html"
