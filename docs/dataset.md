# Dataset requirements

Place source tiles in `images/1150427/RGB_png/`, relative to the extracted
application folder or source project root. Only the RGB PNGs are needed; training
splits and masks are not used by the viewer.

## Input format

- RGB PNG, 8 bits per channel.
- Names follow `<prefix>_<row>_<column>.png`, with positive row and column numbers
  starting at 1. Zero padding is allowed; PNG extensions are case-insensitive.
- All tiles share one prefix, with no duplicate row/column positions.
- Each dimension is greater than 512 and at most 1024 pixels. Cropped outer tiles
  and missing grid cells are supported.
- Grid spacing is **512 pixels**: `x=(column-1)*512`, `y=(row-1)*512`.
- Pixels in horizontal, vertical and diagonal overlaps must be identical.

The application places tiles without warping or blending. Conflicting overlaps
stop creation instead of silently choosing a source. Arbitrary aerial photos or
tiles from another grid require a different stitching workflow.

## Map files

The generated map is stored under `generated/1150427/` as a tiled, lossless
BigTIFF with internal zoom levels and a coverage mask, alongside the tile index.
Black source pixels remain valid; uncovered areas are masked. Coordinates use
the upper-left image origin, X increasing right and Y increasing down. No GPS,
CRS, physical scale or north orientation is inferred.

Keep the generated folder together when moving the application; do not mix files
from separate builds. To start over or reclaim all generated-map space, close
the application and remove `generated/1150427/` **only if you still have the
original tiles**. The next launch can recreate the map from those sources.
