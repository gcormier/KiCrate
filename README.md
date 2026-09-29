# KiCrate

KiCrate generates PCB outlines, KiCad boards and panel files for off-the-shelf electronic enclosures, starting with Hammond.

For each enclosure it produces:

- `<part>.kicad_pcb` (KiCad 10):
  - board outline on Edge.Cuts
  - locked official `MountingHole:*` footprints
  - rule-area keepouts over bosses and screw heads
  - the enclosure's inside wall on User.Drawings
  - notes on User.Comments
- `<part>_edgecuts.dxf` (mm), with layers `OUTLINE`, `HOLES`, `KEEPOUT_TOP`, `KEEPOUT_BOTTOM`, `ENCLOSURE` and `NOTES`

Generated files aren't committed. CI builds them and attaches them to each run as the `kicrate-build` artifact. Release zips and a gallery are planned.

## Data

`data/<mfr>/<part>.yaml` is the source of truth. It's written by hand, by a browser-assisted session or by a scraper, and all three produce the same format. The schema is in `kicrate/schema.py`. The editor JSON Schema at `schema/enclosure.schema.json` is generated with `kicrate schema`.

Conventions:

- **Units:** millimetres throughout.
- **PCB mount origin:** the centre of the board, with +X along the enclosure's length and +Y across its width, viewed from the component side.
- **Holes:** use the manufacturer's exact size. The intended screw goes in `screw` exactly as the manufacturer states it (e.g. `#4 x 1/4" self-tapping`).
- **Footprint choice:** the generator uses the official `MountingHole_<d>mm` footprint whose drill matches exactly, preferring the `_M<n>` variant when the screw is metric. If no official footprint matches, it generates one with the same structure.
- **Provenance:** `provenance` records how the entry was made (`manual`, `assisted` or `scraped`). Keep `verified: false` until someone has checked the result against the drawing or a real part.
- **Sources:** record each source's URL, SHA-256 and retrieval date. Don't commit manufacturer files.

## Use

```sh
uv sync
uv run kicrate validate      # schema + geometry sanity checks
uv run kicrate build         # -> build/<mfr>/<part>/
uv run kicrate drc           # load every board in KiCad and run DRC (needs kicad-cli)
uv run pytest
```

Footprints come from a local KiCad install (`/usr/share/kicad/footprints`, or set `KICRATE_FOOTPRINT_DIR`). If there isn't one, they're fetched from the `kicad-footprints` repo and cached in `~/.cache/kicrate`.

See [docs/PLAN.md](docs/PLAN.md) for the roadmap.
