# Contributing enclosure data

Every enclosure is one YAML file at `data/<mfr>/<part>.yaml`. The schema is in `kicrate/schema.py`. Editors that understand the `yaml-language-server` header get completion from `schema/enclosure.schema.json`.

## Ground rules

- **Units:** millimetres throughout. Convert inch drawings with full precision (`× 25.4`) and keep about three decimals.
- **Coordinates:**
  - **PCB mount:** the origin is the board centre. +X runs along the enclosure's length and +Y across its width, looking down at the component side.
  - **Panel:** the origin is the panel centre, viewed from outside.
- **Manufacturer values first:** use the manufacturer's numbers as given. Anything you had to decide yourself (a clearance, a notch size, a hole size the drawing doesn't state) goes in `notes` as your choice.
- **Holes:** use the exact size, and put the intended screw in `screw` exactly as the manufacturer states it.
- **Sources:** list the drawing, CAD and product-page URLs, with a `sha256` for each file you downloaded. Don't commit the manufacturer's files.
- **Verification:** set `verified: true` only after checking the result (generated SVG at 1:1, or the KiCad board) against a real part or a careful reading of the drawing. Scraped files that are still unverified get overwritten by the weekly scrape. Manual and verified files never do.

## Workflows

### 1. From a manufacturer DWG/DXF (best)

```sh
uv run kicrate extract path/to/drawing.dwg 114.5 60.5   # max PCB length and width in mm
```

This finds a closed outline of that size, at any scale the drawing uses, and prints a `pcb_mounts` snippet with the outline and holes. Paste it into the YAML, then add `boss_diameter`, `screw`, `z`, keepouts and notes. DWG files need LibreDWG's `dwg2dxf` on your PATH.

If no outline is found, the drawing has no PCB view. Measure the posts and bosses from the CAD file instead (circles and dimensions are exact in mm), and design the outline around the bosses yourself, recording it as your choice.

### 2. Scraped drafts

```sh
uv run kicrate scrape hammond --series plastic/1591xx --limit 3
uv run kicrate scrape bud --series nema-ip-rated-boxes/pn-series-nema-box
```

Drafts arrive with `provenance: scraped` and `verified: false`:
- **Hammond:** outlines come from the DWG when the drawing has a PCB view. Otherwise you get the max-PCB rectangle with no holes.
- **Bud:** you get dimensions and links only, with no board yet. Complete the draft, switch `provenance` to `manual`, and check it before marking it verified.

### 3. Browser-assisted (sites with bot protection)

If a site blocks scripts, use a normal browser, optionally with Claude in Chrome, and give it this prompt:

> Open <product URL>. Record the part number, colour/pack variants, external and internal dimensions (mm), and the URLs of the PDF drawing and any DXF/DWG/STEP files. Download the drawing and CAD files and give me their SHA-256. From the drawing, list the PCB mounting features: posts or bosses (centre positions relative to the box centre, diameters, screw size), card slots (width, depth, heights), and anything that limits the board outline (ribs, cover-screw bosses). Quote the drawing's numbers and units exactly, and say which values you inferred.

Then write the YAML with `provenance: assisted` and run `kicrate extract` on the downloaded CAD file.

## Check your entry

```sh
uv run kicrate validate              # schema + geometry sanity (holes inside board, board fits inside)
uv run kicrate build --only <PART>   # build/<mfr>/<part>/: .kicad_pcb, .dxf, 1:1 .svg
uv run kicrate drc                   # if KiCad 10's kicad-cli is installed
uv run pytest
```

Open the SVG and print it at 100% to lay it over the real box, or open the `.kicad_pcb` in KiCad 10.
