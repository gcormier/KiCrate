# KiCrate

KiCrate generates PCB outlines, KiCad boards and panel files for off-the-shelf electronic enclosures. It currently covers Hammond Manufacturing and Bud Industries.

For each enclosure it produces:

| File | Contents |
|---|---|
| `<part>.kicad_pcb` | KiCad 10 board: Edge.Cuts outline, locked official `MountingHole:*` footprints, rule-area keepouts (bosses, screw heads, card slots), the enclosure's inside wall on User.Drawings, notes on User.Comments |
| `<part>_edgecuts.dxf` | The same geometry in mm, on layers `OUTLINE`, `HOLES`, `KEEPOUT_TOP`/`KEEPOUT_BOTTOM`, `ENCLOSURE`, `NOTES` |
| `<part>.svg` | True-scale preview. Print at 100% to get a 1:1 template |
| `<part>_panel_<id>.kicad_pcb` / `.dxf` / `.svg` | Removable end or side panels as a PCB, for CNC/laser cutting, or as a drill template. Shows the usable area and the PCB/slot heights |
| `<part>.json` | The data the files were generated from |

The gallery is published to GitHub Pages from `main`, and tagged releases (`v*`) attach a zip of everything. CI artifacts carry the same files for every branch.

## Data

`data/<mfr>/<part>.yaml` is the source of truth. Three routes produce it, all in the same format:
- **Manual:** a person enters the data.
- **Assisted:** a person uses a browser, optionally with Claude, to collect it.
- **Scraped:** the weekly job opens a PR with draft entries. It never overwrites manual or verified entries.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions and workflows. The schema lives in `kicrate/schema.py`, and editors can use `schema/enclosure.schema.json`.

## Use

```sh
uv sync
uv run kicrate validate                      # schema + geometry sanity checks
uv run kicrate build                         # -> build/<mfr>/<part>/
uv run kicrate drc                           # load every board in KiCad 10 and run DRC (needs kicad-cli)
uv run kicrate site                          # static gallery in site/
uv run kicrate scrape hammond --series plastic/1591xx   # draft data (DWG parsing needs LibreDWG's dwg2dxf)
uv run kicrate scrape bud
uv run kicrate extract drawing.dwg 114.5 60.5          # find a PCB outline + holes in CAD
uv run pytest
```

Footprints come from a local KiCad install (`/usr/share/kicad/footprints`, or set `KICRATE_FOOTPRINT_DIR`). Without one they're fetched from the `kicad-footprints` repository and cached in `~/.cache/kicrate`.

## Repository setup

These are one-time settings:
- **Gallery:** Settings → Pages → Source: **GitHub Actions**.
- **Weekly scrape PRs:** Settings → Actions → General → enable "Allow GitHub Actions to create and approve pull requests".

See [docs/PLAN.md](docs/PLAN.md) for the roadmap.
