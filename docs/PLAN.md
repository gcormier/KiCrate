# KiCrate plan

Generate PCB-ready outlines and panel files for off-the-shelf electronic enclosures.

## Decisions
- **Target:** KiCad 10. Files are written as direct S-expressions (no kiutils / pcbnew dependency).
- **Out of scope:** STEP, both as an extraction source and as an output. There is no 3D-print output for now; DXF and KiCad panels only.
- **Imperial parts:** hole sizes and positions are kept exactly (converted to mm). The intended screw (e.g. `#4-40`) is recorded in the data and on a User layer.
- **Outputs:** not committed to the repo. CI publishes GitHub Release zips and a GitHub Pages gallery.
- **Manufacturer sources:** only the URL, SHA256 and fetch date are stored in the repo. The local cache is gitignored.
- **First series:** Hammond 1455 (extruded, PCB slots, removable end panels) and 1591/1593/1594 (plastic, PCB bosses). Bud comes later.
- **Scraping:** runs in the cloud env once the Hammond and Bud domains are allowlisted. Manual and assisted entry (saved HTML/PDF) is always supported.

## Core principle
YAML in `data/` is the source of truth. Scrapers, humans and browser-assisted sessions all produce the same YAML.
CI validates it and builds the outputs. Scraped entries land as `verified: false` drafts via PR and never overwrite verified data.

## Layout
```
kicrate/
  schema.py            # pydantic models, JSON Schema export
  scrapers/hammond.py  # index + part pages + PDF drawing -> draft YAML
  scrapers/bud.py
  extract/pdf.py       # pdfplumber: dims, hole patterns from drawings
  sexpr.py             # minimal S-expression writer
  gen/dxf.py           # ezdxf: edge cuts, panels
  gen/kicad_pcb.py     # KiCad 10 board: Edge.Cuts, MountingHole:* footprints, keepouts
  gen/panels.py        # end/side/lid panel DXF + .kicad_pcb
  gen/preview.py       # SVG previews
  cli.py               # kicrate scrape | new | validate | build
data/<mfr>/<part>.yaml
.github/workflows/     # validate (PR), build+release+pages (main), weekly scrape -> PR
```

## Data model (per part)
- `mfr`, `part`, `series`, `material`, `url`, `sources[] {url, sha256, fetched}`, `provenance` (scraped|manual|assisted), `verified`, `last_checked`
- `outer`: L x W x H, draft, corner radius
- `pcb_mounts[]`: `type` (bosses | card_guide_slots | floor | lid), outline, keepouts, holes {x, y, drill, boss_d, height, screw}, slot {pcb_thickness, max_width, depth}
- `panels[]`: face, removable, usable cut area, wall thickness, fastener holes
- All values are in mm. The original imperial value is kept where the source gave one. Any field can carry `note`/`confidence`.

## Outputs per part
- `<part>_edgecuts.dxf`: outline, mounting holes, keepout layer
- `<part>.kicad_pcb`: Edge.Cuts, locked official mounting-hole footprints, rule-area keepouts, enclosure outline on User.Drawings, height notes
- `<part>_panel_<face>.dxf` and `.kicad_pcb` for removable panels
- `<part>_drill_template.svg` for non-removable walls
- SVG preview

## CI
- **PR:** schema validation, geometry sanity checks, build, `kicad-cli` DRC/load check (KiCad 10 container), previews uploaded as an artifact
- **main:** build, Release zip, Pages gallery
- **Weekly:** scrape, diff against the repo, open a PR with drafts

## Findings (milestone 1)
- **Hammond part pages** (`/part/<PN>`) list "Max. P.C. Board Length/Width (mm)" and link to:
  - `files/parts/pdf/<PN>.pdf`
  - `files/parts/dxf/<PN>.zip`, which actually contains a DWG
- **DWG → DXF:** LibreDWG `dwg2dxf` works. Drawings are in mm, and most views are 1:1.
- **Scaled views:** some views are scaled, e.g. the 1593L end panel is 1.2:1, shown as DIMENSION `dimlfac` = 0.8333. The extractor must apply that factor.
- **Reading DWG geometry directly** gives exact outlines and hole centres. This caught a fillet I had misread from the PDF, so the scraper should prefer the DWG and fall back to the PDF.
- **Designer choices:** where Hammond gives no PCB outline or hole size (e.g. the 1593L notches, the PCB hole diameter), the data records the value and says in `notes` that it is KiCrate's choice.

## Milestones
1. Schema, CLI, S-expr writer, and a few hand-entered 1455 + 1591 parts. DXF + kicad_pcb generation passing the kicad-cli check.
2. Panel generators (1455 end plates, 159x lids/ends).
3. Hammond scraper: part attributes, DWG → DXF geometry extraction (PDF fallback), producing draft PRs.
4. CI release + Pages gallery.
5. Bud Industries.
