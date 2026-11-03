"""Bounded reconstruction, OS-backed exclusion, and transactional map publication."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import warnings

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning

from . import __version__
from .paths import SCHEMA, atomic_json
from .reconstruction import inventory, geometry, load_rgb, build_mosaic, verify_mosaic

MIB = 1024 * 1024
STRIDE = 512


class BuildCancelled(Exception):
    """The user cancelled a build; no current output was changed."""


@contextmanager
def build_lock(output: Path):
    """Locks are released by the OS even after a hard termination."""
    output.mkdir(parents=True, exist_ok=True)
    stream = (output / ".build.lock").open("a+b")
    acquired = False
    try:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another Aerial Atlas process is building this map. Wait for it to finish.") from error
        acquired = True
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def source_snapshot(source: Path) -> list[dict]:
    return [dict(name=p.name, bytes=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)
            for p in sorted(source.iterdir()) if p.is_file() and p.suffix.lower() == ".png"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def estimate_bytes(extent: dict) -> int:
    # Uncompressed RGB+mask, overview pyramid, 20% margin, and metadata allowance.
    # This is a conservative estimate, not a guarantee against ENOSPC.
    return int(extent["width"] * extent["height"] * 4 * 4 / 3 * 1.2) + 256 * MIB


def preflight(source: Path, output: Path) -> dict:
    if not source.is_dir():
        raise FileNotFoundError(f"Place the dataset's RGB_png folder here, then check again:\n{source}")
    tiles = inventory(source, STRIDE)
    extent = geometry(tiles)
    parent = output
    while not parent.exists():
        parent = parent.parent
    required, free = estimate_bytes(extent), shutil.disk_usage(parent).free
    return dict(count=len(tiles), extent=extent, required_bytes=required, free_bytes=free,
                enough_space=free >= required)


class Progress:
    def __init__(self, path=None, cancel=None, callback=None):
        self.path, self.cancel, self.callback = path, cancel, callback
        self.last = 0.0
        self.stage = ""

    def __call__(self, stage, done=0, total=0, message=""):
        if stage != "complete" and self.cancel and self.cancel.exists():
            raise BuildCancelled("Map creation cancelled. The previous map is unchanged.")
        now = time.monotonic()
        if stage == self.stage and now - self.last < .1 and done != total:
            return
        self.last, self.stage = now, stage
        value = dict(stage=stage, done=done, total=total, message=message)
        if self.path:
            atomic_json(self.path, value)
        if self.callback:
            self.callback(value)


def validate_overlaps(tiles, staging: Path, progress: Progress) -> dict:
    """An LRU bounds decoded sources; no complete row of arrays is retained."""
    cache = OrderedDict()
    used = peak = pairs = compared = 0
    positions = {(tile.row, tile.col): tile for tile in tiles}

    def pixels(tile):
        nonlocal used, peak
        key = (tile.row, tile.col)
        if key in cache:
            value = cache.pop(key)
            cache[key] = value
            return value
        value = load_rgb(tile)
        while cache and used + value.nbytes > 64 * MIB:
            _, old = cache.popitem(last=False)
            used -= old.nbytes
        cache[key] = value
        used += value.nbytes
        peak = max(peak, used)
        return value

    with (staging / "overlap_validation.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["tile_a", "tile_b", "compared_pixels", "mismatched_pixels"])
        for number, tile in enumerate(tiles, 1):
            progress("overlaps", number, len(tiles))
            a = pixels(tile)
            for delta in ((0, -1), (-1, -1), (-1, 0), (-1, 1)):
                other = positions.get((tile.row + delta[0], tile.col + delta[1]))
                if other is None:
                    continue
                b = pixels(other)
                x0, y0 = max(tile.x, other.x), max(tile.y, other.y)
                x1, y1 = min(tile.x + tile.width, other.x + other.width), min(tile.y + tile.height, other.y + other.height)
                if x1 <= x0 or y1 <= y0:
                    continue
                aa = a[y0-tile.y:y1-tile.y, x0-tile.x:x1-tile.x]
                bb = b[y0-other.y:y1-other.y, x0-other.x:x1-other.x]
                mismatch = int(np.count_nonzero(np.any(aa != bb, axis=2)))
                count = (x1-x0) * (y1-y0)
                writer.writerow([other.path.name, tile.path.name, count, mismatch])
                pairs += 1
                compared += count
                if mismatch:
                    raise ValueError(f"Overlapping pixels disagree: {other.path.name} and {tile.path.name}. "
                                     "Use tiles from the same unchanged orthophoto; no map was published.")
    with (staging / "tile_index.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["filename", "row", "column", "x", "y", "width", "height", "source_file_sha256"])
        for tile in tiles:
            writer.writerow([tile.path.name, tile.row, tile.col, tile.x, tile.y, tile.width, tile.height, tile.sha256])
    return dict(pairs=pairs, compared_pixels=compared, mismatched_pixels=0, peak_source_cache_bytes=peak)


def build(source: Path, output: Path, progress: Progress | None = None) -> dict:
    source, output = source.resolve(), output.resolve()
    progress = progress or Progress()
    started = time.monotonic()
    staging = None
    with build_lock(output):
        # Only interrupted staging generations are disposable; published generations stay intact.
        for path in output.glob(".staging-*"):
            if re.fullmatch(r"\.staging-[0-9a-f]{32}", path.name) and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        try:
            progress("inventory")
            before = source_snapshot(source)
            info = preflight(source, output)
            if not info["enough_space"]:
                raise OSError("Not enough free disk space for the estimated map and overviews. Free space and try again.")
            tiles = inventory(source, STRIDE)
            build_id = uuid.uuid4().hex
            staging = output / (".staging-" + build_id)
            staging.mkdir()
            overlap = validate_overlaps(tiles, staging, progress)
            destination = staging / "rgb_world_map.tif"
            with warnings.catch_warnings(), rasterio.Env(
                GDAL_TIFF_INTERNAL_MASK=True, GDAL_CACHEMAX=128 * MIB, GDAL_NUM_THREADS="2",
                COMPRESS_OVERVIEW="DEFLATE", PREDICTOR_OVERVIEW="2", BIGTIFF_OVERVIEW="YES",
            ):
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                build_mosaic(tiles, destination, info["extent"], STRIDE, progress, build_id)
                verification = verify_mosaic(tiles, destination, info["extent"], progress)
            if source_snapshot(source) != before:
                raise RuntimeError("Source files changed during creation. Keep the dataset unchanged and try again.")
            progress("publishing")
            report = dict(schema=SCHEMA, status="complete", app_version=__version__, build_id=build_id,
                          source_count=len(tiles), sources=before, extent=info["extent"], stride=STRIDE,
                          overlap_validation=overlap, verification=verification,
                          tiff_bytes=destination.stat().st_size, index_sha256=sha256(staging / "tile_index.csv"),
                          elapsed_seconds=round(time.monotonic()-started, 3),
                          resource_limits=dict(gdal_cache_mib=128, source_cache_mib=64, compression_threads=2))
            atomic_json(staging / "manifest.json", report)
            generation = "build-" + build_id
            staging.rename(output / generation)
            staging = None
            atomic_json(output / "current.json", dict(schema=SCHEMA, directory=generation))
            progress("complete", 1, 1, "Map created and verified.")
            return report
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)


def current_map(output: Path, source: Path | None = None) -> dict:
    """Read small metadata and TIFF headers; never hash all source PNGs at startup."""
    pointer = json.loads((output / "current.json").read_text(encoding="utf-8"))
    if pointer.get("schema") != SCHEMA:
        raise ValueError("Unsupported map record version. Rebuild with this version of Aerial Atlas.")
    directory = pointer.get("directory", "")
    if not re.fullmatch(r"build-[0-9a-f]{32}", directory):
        raise ValueError("Invalid map generation record. Rebuild the map.")
    folder = output / directory
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("status") != "complete":
        raise ValueError("The map was not completed by a supported version. Rebuild it.")
    image, index = folder / "rgb_world_map.tif", folder / "tile_index.csv"
    if image.stat().st_size != manifest["tiff_bytes"] or sha256(index) != manifest["index_sha256"]:
        raise ValueError("The map or tile index is incomplete or has changed. Rebuild it.")
    from .mosaic_viewer import TileIndex
    tiles = TileIndex(index)
    with warnings.catch_warnings(), rasterio.Env(GDAL_CACHEMAX=8 * MIB):
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(image) as dataset:
            if ((dataset.width, dataset.height) != (tiles.width, tiles.height)
                or dataset.count != 3 or dataset.dtypes != ("uint8",)*3
                or dataset.tags().get("aerial_atlas_build") != manifest["build_id"]
                or len(tiles.tiles) != manifest["source_count"]
                or [dataset.width, dataset.height] != [manifest["extent"]["width"], manifest["extent"]["height"]]
                or dataset.overviews(1) != manifest["verification"]["internal_overviews"]):
                raise ValueError("Map metadata and tile index do not match. Rebuild the map.")
            dataset.read(window=rasterio.windows.Window(0, 0, 1, 1))
    snapshot = source_snapshot(source) if source is not None and source.is_dir() else []
    changed = bool(snapshot) and snapshot != manifest["sources"]
    return dict(image=image, index=index, manifest=manifest, changed=changed)
