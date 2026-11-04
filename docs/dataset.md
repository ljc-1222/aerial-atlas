# Dataset requirements

The private datasets are supplied separately. Download their original ZIPs and
TIFFs into `images/`, or select the folder where they were downloaded in the app.
Preparation automatically extracts recognized imagery. Original ZIPs are kept,
existing different files are never overwritten, and interrupted imports can be
retried. Training splits, annotation JSON and semantic masks are left alone.

The date buttons show the selected survey and a short status: **Prepared** means
a saved map/index exists; **Downloaded** means source tiles and the required TIFF
are available to prepare; **Missing files** and **Check files** lead to setup or
error recovery. These are quick hints; files are checked again before opening.
Missing surveys stay selectable so their download folder can be chosen.

## Shared layout

```text
images/
  1150427/
    tiles_png_1024/20260427_nodata_<row>_<column>.png
  1150618/
    tiles_png_1024/1150618_tile_<row>_<column>.png
    1150618_input_orthomosaic-001.tif
  1150806/
    tiles_png_1024/1150806_tile_<row>_<column>.png
    1150806_input_orthomosaic-001.tif
```

The TIFF may also remain directly in the selected download folder. The app
imports ZIPs directly in that folder or a date folder. It does not unpack ZIPs
inside `archives/` or recursively search unrelated folders. Completed imports
are recorded in `.imports.json` so later launches do not decode them again.

An existing `1150427/RGB_png/` folder is renamed to `tiles_png_1024/` automatically.
Its image filenames, bytes and cropped edge sizes are preserved. If both tile
folders contain files, preparation stops rather than choosing an incomplete
set or overwriting images. ZIPs may contain April tiles under `RGB_png/`,
`image/RGB_png/`, or `1150427/RGB_png/`; standard date-prefixed layouts are also
recognized.

## Tile geometry and maps

| Survey | Native numbering | Placement | Main image |
|---|---|---|---|
| `1150427` | Starts at 1 | `x=(column-1)*512`, `y=(row-1)*512` | Reconstructed RGB BigTIFF |
| `1150618` | Starts at 0 | `x=column*512`, `y=row*512` | Supplied source TIFF |
| `1150806` | Starts at 0 | `x=column*512`, `y=row*512` | Supplied source TIFF |

Tiles are RGB PNGs, 8 bits per channel, with a common filename prefix and unique
row/column pairs. Dimensions must be greater than 512 and at most 1024; April's
cropped edges are supported without padding. A full 1024-pixel tile overlaps its
neighbor by 512 pixels. Missing grid cells are allowed.

April is reconstructed without warping or blending. All horizontal, vertical
and diagonal overlap pixels must agree. Its generated TIFF has a coverage mask
and internal zoom levels. Black source pixels remain valid, and no geographic
CRS is invented.

June and August PNGs have individual display stretches, so their overlapping
colors need not agree. The app builds a small tile index and reads the supplied
8-band uint16 TIFF instead of stitching these PNGs. Display uses RGB bands 1-3
on one fixed 0-65535 scale and band 8 as validity. Other bands and georeferencing
stay in the original TIFF. Supplied PNG colors may differ from the full map.
The raster's full extent is retained, including edges with no exported PNG tile.
The labeled overview PNG is not used as the base map.

## Generated files and command line

Each survey has its own `generated/<survey>/` directory. A verified generation
contains the tile index and manifest; April also contains its reconstructed map.
Publication is atomic, and cancellation leaves the previous generation intact.
The existing April map can be reused after the tile directory is adapted.

For scripted use, run from a source checkout:

```powershell
.\.venv\Scripts\python.exe -m aerial_atlas --dataset 1150618 --data-dir "D:\survey-downloads" --build
```

`--data-dir` selects the folder containing the downloads, not `tiles_png_1024`.
For development, an empty project `images/` directory also recognizes a sibling
`../images/` containing the surveys. The data and generated maps stay out of Git.

Keep source TIFFs available when opening June or August. Move `images/` and
`generated/` together to preserve relative TIFF references, or select the new
download folder. Views use image pixels; switching dates does not perform
geographic alignment or cross-date change detection.
