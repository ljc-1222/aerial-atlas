"""Pixel-exact reconstruction primitives. No geographic CRS is assigned."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import io
import re
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import Window

@dataclass
class Tile:
    path: Path
    row: int
    col: int
    width: int
    height: int
    x: int
    y: int
    sha256: str = ""

    @property
    def window(self) -> Window:
        return Window(self.x, self.y, self.width, self.height)


def log(message: str) -> None:
    pass


def load_rgb(tile: Tile, check_hash: bool = False) -> np.ndarray:
    data = tile.path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if check_hash and digest != tile.sha256:
        raise RuntimeError(f"Source changed after validation: {tile.path.name}")
    tile.sha256 = digest
    with Image.open(io.BytesIO(data)) as image:
        if image.mode != "RGB" or image.size != (tile.width, tile.height):
            raise ValueError(f"Unexpected source mode or dimensions: {tile.path.name}")
        pixels = np.array(image, dtype=np.uint8)
    return pixels


def inventory(source: Path, stride: int, index_base: int = 1) -> list[Tile]:
    if index_base not in (0, 1):
        raise ValueError("Grid index base must be 0 or 1")
    tiles = []
    prefixes = set()
    keys = set()
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        match = re.fullmatch(r"(.+)_(\d+)_(\d+)\.png", path.name, re.IGNORECASE)
        if match is None:
            raise ValueError(f"Cannot parse source name: {path.name}")
        prefix, row, col = match[1], int(match[2]), int(match[3])
        if row < index_base or col < index_base or (row, col) in keys:
            raise ValueError(f"Invalid or duplicate grid coordinate: {path.name}")
        prefixes.add(prefix)
        keys.add((row, col))
        with Image.open(path) as image:
            width, height = image.size
            if image.mode != "RGB" or not (stride < width <= stride * 2 and stride < height <= stride * 2):
                raise ValueError(f"Unsupported source dimensions or mode: {path.name}")
        tiles.append(Tile(path, row, col, width, height, (col - index_base) * stride, (row - index_base) * stride))
    if not tiles or len(prefixes) != 1:
        raise ValueError("Expected a nonempty set of tiles from one filename prefix")
    return sorted(tiles, key=lambda tile: (tile.row, tile.col))


def geometry(tiles: list[Tile]) -> dict:
    width = max(tile.x + tile.width for tile in tiles)
    height = max(tile.y + tile.height for tile in tiles)
    xs = sorted({0, width} | {x for t in tiles for x in (t.x, t.x + t.width)})
    ys = sorted({0, height} | {y for t in tiles for y in (t.y, t.y + t.height)})
    xi, yi = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}
    counts = np.zeros((len(ys) - 1, len(xs) - 1), dtype=np.uint8)
    for tile in tiles:
        counts[yi[tile.y]:yi[tile.y + tile.height], xi[tile.x]:xi[tile.x + tile.width]] += 1
    areas = np.diff(ys).astype(np.int64)[:, None] * np.diff(xs).astype(np.int64)[None, :]
    covered = int(areas[counts > 0].sum())
    return {
        "width": width,
        "height": height,
        "canvas_pixels": width * height,
        "covered_pixels": covered,
        "uncovered_pixels": width * height - covered,
        "coverage_fraction": covered / (width * height),
        "maximum_overlapping_tiles": int(counts.max()),
        "geographic_crs": None,
        "coordinate_system": "image pixels; x increases rightward and y increases downward",
    }


def build_mosaic(tiles: list[Tile], destination: Path, extent: dict, stride: int,
                 progress=lambda *args: None, build_id="") -> list[int]:
    width, height = extent["width"], extent["height"]
    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 3, "dtype": "uint8",
        "tiled": True, "blockxsize": 512, "blockysize": 512,
        "compress": "deflate", "predictor": 2, "zlevel": 6,
        "photometric": "RGB", "interleave": "pixel", "BIGTIFF": "YES", "SPARSE_OK": True,
    }
    with rasterio.open(destination, "w", **profile) as dataset:
        dataset.update_tags(
            aerial_atlas_build=build_id,
            reconstruction="Exact placement of numbered orthophoto tiles; no warping or blending",
            placement_formula=f"x=(column-1)*{stride}; y=(row-1)*{stride}",
            source_count=str(len(tiles)), coordinate_system="image pixels; no geographic CRS assigned",
            mask_meaning="255 where at least one source tile exists; 0 elsewhere; source black pixels are retained",
        )
        for y in range(0, height, 512):
            progress("mask", y, height)
            rows = min(512, height - y)
            dataset.write_mask(np.zeros((rows, width), dtype=np.uint8), window=Window(0, y, width, rows))
        log("WRITE coverage mask initialized; writing lossless RGB tiles")
        for index, tile in enumerate(tiles, 1):
            progress("writing", index, len(tiles))
            pixels = load_rgb(tile, check_hash=True)
            dataset.write(pixels.transpose(2, 0, 1), window=tile.window)
            dataset.write_mask(np.full((tile.height, tile.width), 255, dtype=np.uint8), window=tile.window)
            if index == 1 or index % 100 == 0 or index == len(tiles):
                log(f"WRITE {index}/{len(tiles)} sources")
    factors = [2, 4, 8, 16, 32, 64]
    progress("overviews", 0, 0)
    log(f"OVERVIEWS building internal reduced-resolution layers {factors}")
    with rasterio.open(destination, "r+") as dataset:
        dataset.build_overviews(factors, Resampling.average)
        dataset.update_tags(ns="rio_overview", resampling="average")
    log("OVERVIEWS complete")
    return factors


def verify_mosaic(tiles: list[Tile], destination: Path, extent: dict,
                  progress=lambda *args: None) -> dict:
    log("VERIFY reading back every source footprint from the completed TIFF")
    with destination.open("rb") as stream:
        signature = stream.read(4)
    if signature not in (b"II+\x00", b"MM\x00+"):
        raise RuntimeError("Output is not BigTIFF")
    summary = {"tiles_checked": 0, "source_pixel_comparisons": 0, "mismatched_pixels": 0, "mask_covered_pixels": 0}
    with rasterio.open(destination) as dataset:
        if (dataset.width, dataset.height, dataset.count) != (extent["width"], extent["height"], 3):
            raise RuntimeError("Incorrect output dimensions or band count")
        if dataset.crs is not None or dataset.nodata is not None:
            raise RuntimeError("Unexpected geographic CRS or RGB nodata value")
        if not dataset.profile.get("tiled") or dataset.compression.value.upper() != "DEFLATE":
            raise RuntimeError("Expected a tiled lossless DEFLATE TIFF")
        for index, tile in enumerate(tiles, 1):
            progress("verifying", index, len(tiles))
            expected = load_rgb(tile, check_hash=True)
            actual = dataset.read(window=tile.window).transpose(1, 2, 0)
            if not np.array_equal(expected, actual):
                mismatch = int(np.count_nonzero(np.any(expected != actual, axis=2)))
                raise RuntimeError(f"Output differs from {tile.path.name}: {mismatch} pixels")
            mask = dataset.dataset_mask(window=tile.window)
            if not np.all(mask == 255):
                raise RuntimeError(f"Source footprint is masked: {tile.path.name}")
            summary["tiles_checked"] += 1
            summary["source_pixel_comparisons"] += tile.width * tile.height
            if index == 1 or index % 100 == 0 or index == len(tiles):
                log(f"VERIFY {index}/{len(tiles)} sources; exact RGB and coverage matches")
        for y in range(0, dataset.height, 512):
            progress("coverage", y, dataset.height)
            mask = dataset.dataset_mask(window=Window(0, y, dataset.width, min(512, dataset.height - y)))
            summary["mask_covered_pixels"] += int(np.count_nonzero(mask))
        if summary["mask_covered_pixels"] != extent["covered_pixels"]:
            raise RuntimeError("Output coverage mask differs from source rectangle union")
        if len(dataset.files) != 1:
            raise RuntimeError(f"Expected one self-contained TIFF, found sidecars: {dataset.files}")
        summary["internal_overviews"] = dataset.overviews(1)
        summary["compression"] = dataset.compression.value
        summary["block_shapes"] = dataset.block_shapes
        summary["bigtiff"] = True
    return summary

