"""Local orthophoto viewer with filename lookup and bounded, latest-only rendering."""

from __future__ import annotations

import argparse
from collections import OrderedDict, deque
import csv
import ctypes
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import threading
import time
import warnings

# Array slicing and image display do not need BLAS worker pools. Configure before
# importing NumPy to avoid hundreds of MiB of unused thread-stack commitments on
# Windows. These settings affect only this viewer process and its children.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np
from PIL import Image, ImageTk
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window
import tkinter as tk
from tkinter import messagebox
from .viewer_preferences import SettingsStore
from .viewer_ui import ViewerUI
from .viewer_navigation import ViewHistory, ViewState
from .raster import read_display, validate_raster



MIB = 1024 * 1024
CONTEXT_FACTOR = 1.5
BACKGROUND = (232, 238, 234, 255)
OVERVIEW_PIXELS = 1_000_000
OVERSCAN = 128


@dataclass(frozen=True)
class Tile:
    filename: str
    row: int
    column: int
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def identifier(self) -> str:
        return f"{self.row:02}_{self.column:02}"


class TileIndex:
    def __init__(self, path: Path, extent: tuple[int, int] | None = None):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            self.tiles = [Tile(row["filename"], *(int(row[key]) for key in ("row", "column", "x", "y", "width", "height"))) for row in csv.DictReader(stream)]
        if not self.tiles:
            raise ValueError("The tile index is empty.")
        self.tiles.sort(key=lambda tile: (tile.row, tile.column))
        self.by_name = {tile.filename.casefold(): tile for tile in self.tiles}
        self.by_stem = {Path(tile.filename).stem.casefold(): tile for tile in self.tiles}
        self.by_position = {(tile.row, tile.column): tile for tile in self.tiles}
        if len(self.by_name) != len(self.tiles) or len(self.by_position) != len(self.tiles):
            raise ValueError("The tile index contains duplicate filenames or grid positions.")
        if any(t.row < 0 or t.column < 0 or min(t.x,t.y) < 0 or min(t.width,t.height) < 1 for t in self.tiles):
            raise ValueError("The tile index contains invalid coordinates or dimensions.")
        self.width = max(tile.x + tile.width for tile in self.tiles)
        self.height = max(tile.y + tile.height for tile in self.tiles)
        if extent is not None:
            if len(extent) != 2 or any(not isinstance(value, int) or value < 1 for value in extent):
                raise ValueError("Invalid raster extent.")
            if self.width > extent[0] or self.height > extent[1]:
                raise ValueError("Tile footprints extend outside the raster.")
            self.width, self.height = extent
        self.rows = sorted({tile.row for tile in self.tiles})

    @staticmethod
    def normalize(text: str) -> str:
        return text.strip().strip('"').replace("\\", "/").rsplit("/", 1)[-1].casefold()

    def find(self, text: str) -> Tile | None:
        text = self.normalize(text)
        exact = self.by_name.get(text) or self.by_stem.get(text)
        if exact is not None:
            return exact
        position = re.fullmatch(r"(\d+)\s*[_，,]\s*(\d+)(?:\.png)?", text)
        return self.by_position.get(tuple(map(int, position.groups()))) if position else None

    def suggestions(self, text: str) -> list[Tile]:
        normalized = self.normalize(text)
        if not normalized:
            return self.tiles
        exact = self.find(text)
        if exact is not None:
            return [exact]
        return [tile for tile in self.tiles if normalized in tile.filename.casefold()]

    def at_point(self, x: float, y: float) -> list[Tile]:
        """Return every covering source footprint, with half-open pixel bounds.

        This metadata-only lookup runs on an explicit pick, never on each paint.
        Overlapping source tiles remain separate selectable results.
        """
        return [tile for tile in self.tiles if tile.x <= x < tile.x + tile.width
                and tile.y <= y < tile.y + tile.height]


class TileBrowser:
    """Selection never rewrites the user's independent browse/filter scope."""

    def __init__(self, index: TileIndex):
        self.index = index
        self.row: int | None = None
        self.tiles = index.tiles
        self.positions = {tile.filename: position for position, tile in enumerate(self.tiles)}

    def filter(self, text: str = "", row: int | None = None) -> list[Tile]:
        self.row = row
        self.tiles = [tile for tile in self.index.suggestions(text) if row is None or tile.row == row]
        self.positions = {tile.filename: position for position, tile in enumerate(self.tiles)}
        return self.tiles

    def position(self, selected: Tile | None) -> int | None:
        return self.positions.get(selected.filename) if selected is not None else None

    def neighbor(self, selected: Tile | None, direction: int) -> Tile | None:
        if not self.tiles:
            return None
        current = self.position(selected)
        target = (0 if direction > 0 else len(self.tiles) - 1) if current is None else current + direction
        return self.tiles[target] if 0 <= target < len(self.tiles) else None


@dataclass
class Camera:
    cx: float
    cy: float
    scale: float = 1.0

    def fit(self, cx: float, cy: float, width: float, height: float, screen_width: int, screen_height: int) -> None:
        self.cx, self.cy = cx, cy
        self.scale = min(screen_width / width, screen_height / height)

    def locate(self, tile: Tile, screen_width: int, screen_height: int) -> None:
        self.fit(*tile.center, tile.width * CONTEXT_FACTOR, tile.height * CONTEXT_FACTOR, screen_width, screen_height)

    def world_at(self, sx: float, sy: float, width: int, height: int) -> tuple[float, float]:
        return self.cx + (sx - width / 2) / self.scale, self.cy + (sy - height / 2) / self.scale

    def screen_at(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        return (x - self.cx) * self.scale + width / 2, (y - self.cy) * self.scale + height / 2

    def zoom(self, factor: float, sx: float, sy: float, width: int, height: int, minimum: float, maximum: float = 16.0) -> None:
        x, y = self.world_at(sx, sy, width, height)
        self.scale = min(maximum, max(minimum, self.scale * factor))
        self.cx = x - (sx - width / 2) / self.scale
        self.cy = y - (sy - height / 2) / self.scale


class ImageCache:
    """One byte-counted LRU; each entry is an RGBA image of bounded dimensions."""

    def __init__(self, limit_bytes: int):
        self.limit_bytes = limit_bytes
        self.bytes_used = 0
        self.entries: OrderedDict[tuple, Image.Image] = OrderedDict()

    def get(self, key: tuple) -> Image.Image | None:
        image = self.entries.get(key)
        if image is not None:
            self.entries.move_to_end(key)
        return image

    def put(self, key: tuple, image: Image.Image) -> None:
        size = image.width * image.height * 4
        if key in self.entries:
            previous = self.entries.pop(key)
            self.bytes_used -= previous.width * previous.height * 4
        if size > self.limit_bytes:
            return
        while self.entries and self.bytes_used + size > self.limit_bytes:
            _, previous = self.entries.popitem(last=False)
            self.bytes_used -= previous.width * previous.height * 4
        self.entries[key] = image
        self.bytes_used += size

    def clear(self) -> None:
        self.entries.clear()
        self.bytes_used = 0


@dataclass(frozen=True)
class RenderRequest:
    serial: int
    cx: float
    cy: float
    scale: float
    width: int
    height: int

    @property
    def key(self) -> tuple:
        return self.cx, self.cy, self.scale, self.width, self.height


@dataclass
class RenderResult:
    request: RenderRequest
    image: Image.Image | None = None
    error: str | None = None
    cache_hit: bool = False
    milliseconds: float = 0.0


@dataclass(frozen=True)
class WorldFrame:
    """An immutable display texture with explicit image-pixel world bounds."""

    image: Image.Image
    left: float
    top: float
    width: float
    height: float

    @classmethod
    def from_result(cls, result: RenderResult) -> WorldFrame:
        request = result.request
        width, height = request.width / request.scale, request.height / request.scale
        return cls(result.image, request.cx - width / 2, request.cy - height / 2, width, height)

    def intersects(self, camera: Camera, width: int, height: int) -> bool:
        x, y = camera.world_at(0, 0, width, height)
        return (x < self.left + self.width and x + width / camera.scale > self.left
                and y < self.top + self.height and y + height / camera.scale > self.top)

    def contains(self, camera: Camera, width: int, height: int) -> bool:
        x, y = camera.world_at(0, 0, width, height)
        return (x >= self.left - 1e-6 and y >= self.top - 1e-6
                and x + width / camera.scale <= self.left + self.width + 1e-6
                and y + height / camera.scale <= self.top + self.height + 1e-6)


def sample_frame(frame: WorldFrame, camera: Camera, width: int, height: int, interactive: bool, transparent: bool = False) -> Image.Image:
    """Reproject retained imagery immediately; never hide it during a gesture."""
    x, y = camera.world_at(0, 0, width, height)
    sx, sy = frame.image.width / frame.width, frame.image.height / frame.height
    affine = (sx / camera.scale, 0, (x - frame.left) * sx,
              0, sy / camera.scale, (y - frame.top) * sy)
    # At a fixed zoom most pans are integer-pixel crops inside the overscan.
    # Avoid two affine passes and alpha conversions for this common case.
    a, _, c, _, e, f = affine
    if (abs(a - 1) < 1e-8 and abs(e - 1) < 1e-8 and abs(c - round(c)) < 1e-6 and abs(f - round(f)) < 1e-6
            and c >= -1e-6 and f >= -1e-6 and c + width <= frame.image.width + 1e-6 and f + height <= frame.image.height + 1e-6):
        left, top = round(c), round(f)
        return frame.image.crop((left, top, left + width, top + height))
    return frame.image.transform(
        (width, height), Image.Transform.AFFINE, affine,
        Image.Resampling.NEAREST if interactive else Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0) if transparent else BACKGROUND,
    )


def compose_view(overview: WorldFrame | None, detail: WorldFrame | None, camera: Camera, width: int, height: int, interactive: bool = False, background=BACKGROUND, opaque: bool = False) -> Image.Image:
    # Transparent data gaps let either UI theme change without rereading the map.
    if detail is not None and detail.contains(camera, width, height):
        texture = sample_frame(detail, camera, width, height, interactive, transparent=True)
        base = Image.new("RGB" if opaque else "RGBA", (width, height), background[:3] if opaque else background)
        if opaque:
            base.paste(texture, (0, 0), texture.getchannel("A"))
        else:
            base.alpha_composite(texture)
        return base
    base = Image.new("RGB" if opaque else "RGBA", (width, height), background[:3] if opaque else background)
    if overview:
        texture = sample_frame(overview, camera, width, height, interactive, transparent=True)
        if opaque:
            base.paste(texture, (0, 0), texture.getchannel("A"))
        else:
            base.alpha_composite(texture)
    if detail is not None and detail.intersects(camera, width, height):
        overlay = sample_frame(detail, camera, width, height, interactive, transparent=True)
        if opaque:
            base.paste(overlay, (0, 0), overlay.getchannel("A"))
        else:
            base.alpha_composite(overlay)
    return base


def render_region(dataset, request: RenderRequest, pixel_limit: int) -> Image.Image:
    """Read only intersecting blocks/overviews, with a bounded output buffer.

    Clipping is explicit so a view centered at the map edge still uses native
    overviews instead of constructing a boundless virtual raster.
    """
    ratio = min(1.0, math.sqrt(pixel_limit / (request.width * request.height)))
    width = max(1, int(request.width * ratio))
    height = max(1, int(request.height * ratio))
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    world_width, world_height = request.width / request.scale, request.height / request.scale
    x0, y0 = request.cx - world_width / 2, request.cy - world_height / 2
    left, top = max(0.0, x0), max(0.0, y0)
    right, bottom = min(float(dataset.width), x0 + world_width), min(float(dataset.height), y0 + world_height)
    if right <= left or bottom <= top:
        return result
    dx0 = max(0, round((left - x0) / world_width * width))
    dy0 = max(0, round((top - y0) / world_height * height))
    dx1 = min(width, round((right - x0) / world_width * width))
    dy1 = min(height, round((bottom - y0) / world_height * height))
    if dx1 <= dx0 or dy1 <= dy0:
        return result
    region = Window(left, top, right - left, bottom - top)
    rgba = read_display(dataset, region, (dy1 - dy0, dx1 - dx0))
    foreground = Image.fromarray(rgba)
    result.alpha_composite(foreground, (dx0, dy0))
    return result


class RenderWorker(threading.Thread):
    """One reader, one pending request, one completed frame; no unbounded queues."""

    def __init__(self, path: Path, extent: tuple[int, int], cache_mb: int = 64, gdal_cache_mb: int = 64, pixel_limit: int = 2_000_000):
        super().__init__(name="mosaic-reader", daemon=True)
        self.path, self.extent = path, extent
        self.cache = ImageCache(cache_mb * MIB)
        self.gdal_cache_mb, self.pixel_limit = gdal_cache_mb, pixel_limit
        self.condition = threading.Condition()
        self.pending: RenderRequest | None = None
        self.completed: RenderResult | None = None
        self.serial = 0
        self.stopping = False
        self.clear_requested = False
        self.fatal_error: str | None = None
        self.overview: WorldFrame | None = None
        self.statistics = {"reads": 0, "hits": 0, "discarded": 0, "cache_bytes": 0, "cache_entries": 0, "clears": 0, "overview_bytes": 0}

    def submit(self, camera: Camera, width: int, height: int, padding: int = 0) -> int:
        with self.condition:
            self.serial += 1
            self.pending = RenderRequest(self.serial, camera.cx, camera.cy, camera.scale, width + 2 * padding, height + 2 * padding)
            self.condition.notify()
            return self.serial

    def clear_cache(self) -> None:
        with self.condition:
            self.clear_requested = True
            self.condition.notify()

    def take_result(self) -> RenderResult | None:
        with self.condition:
            result, self.completed = self.completed, None
            return result

    def stats(self) -> dict:
        with self.condition:
            return dict(self.statistics, pending=int(self.pending is not None))

    def get_overview(self) -> WorldFrame | None:
        with self.condition:
            return self.overview

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.pending = self.completed = None
            self.condition.notify()

    def run(self) -> None:
        dataset = None
        try:
            with warnings.catch_warnings(), rasterio.Env(GDAL_CACHEMAX=self.gdal_cache_mb * MIB, GDAL_NUM_THREADS="1"):
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                dataset = rasterio.open(self.path, "r", sharing=False)
                validate_raster(dataset)
                if (dataset.width, dataset.height) != self.extent:
                    raise ValueError("主圖尺寸或色彩通道與索引不一致。")
                # A small, fixed-size navigation texture fills exposed regions
                # during any pan/zoom, including jumps outside the last detail ROI.
                overview = render_region(dataset, RenderRequest(0, dataset.width / 2, dataset.height / 2, 1.0, dataset.width, dataset.height), OVERVIEW_PIXELS)
                with self.condition:
                    self.overview = WorldFrame(overview, 0, 0, dataset.width, dataset.height)
                    self.statistics["overview_bytes"] = overview.width * overview.height * 4
                while True:
                    with self.condition:
                        self.condition.wait_for(lambda: self.stopping or self.pending is not None or self.clear_requested)
                        if self.stopping:
                            break
                        clear = self.clear_requested
                        self.clear_requested = False
                        request, self.pending = self.pending, None
                    if clear:
                        self.cache.clear()
                        # Closing this sole reader releases its GDAL block cache too.
                        dataset.close()
                        dataset = rasterio.open(self.path, "r", sharing=False)
                        with self.condition:
                            self.statistics.update(cache_bytes=0, cache_entries=0)
                            self.statistics["clears"] += 1
                    if request is None:
                        continue
                    started = time.perf_counter()
                    try:
                        image = self.cache.get(request.key)
                        hit = image is not None
                        if image is None:
                            image = render_region(dataset, request, self.pixel_limit)
                        with self.condition:
                            if self.stopping:
                                break
                            if self.clear_requested:
                                self.statistics["discarded"] += 1
                                continue
                        if not hit:
                            self.cache.put(request.key, image)
                        # Keep capped-size textures. The UI reprojects them to the
                        # CURRENT camera, so completed earlier work stays useful
                        # while newer requests replace the one pending slot.
                        result = RenderResult(request, image, cache_hit=hit, milliseconds=(time.perf_counter() - started) * 1000)
                        with self.condition:
                            if not self.stopping:
                                self.completed = result
                            else:
                                self.statistics["discarded"] += 1
                            self.statistics["hits" if hit else "reads"] += 1
                            self.statistics.update(cache_bytes=self.cache.bytes_used, cache_entries=len(self.cache.entries))
                    except Exception as error:
                        with self.condition:
                            if request.serial == self.serial:
                                self.completed = RenderResult(request, error=str(error))
        except Exception as error:
            with self.condition:
                self.fatal_error = str(error)
        finally:
            if dataset is not None:
                dataset.close()
            self.cache.clear()


class MosaicViewer(ViewerUI):
    def __init__(self, root: tk.Tk, index: TileIndex, image_path: Path, args):
        self.root, self.index, self.args = root, index, args
        self.settings_store = SettingsStore(getattr(args, "settings_path", None))
        self.preferences = self.settings_store.load()
        if getattr(args, "restored_view", None):
            self.preferences.last_view = args.restored_view
        self.map_key = getattr(args, "map_identity", None) or str(image_path.resolve()).casefold()
        self.history = ViewHistory()
        self.navigation_active = False
        self.camera = Camera(index.width / 2, index.height / 2)
        self.selected: Tile | None = None
        self.browser = TileBrowser(index)
        self.filtered = index.tiles
        self.photo: ImageTk.PhotoImage | None = None
        self.frame_request: RenderRequest | None = None
        self.detail_frame: WorldFrame | None = None
        self.overview: WorldFrame | None = None
        self.image_item = None
        self.display_request: RenderRequest | None = None
        self.display_key = None
        self.texture_uploads = self.translated_paints = self.reused_views = 0
        self.drag_position = None
        self.render_after = self.filter_after = None
        self.paint_after = self.settle_after = None
        self.poll_after = self.startup_after = None
        self.last_interaction = 0.0
        self.interaction_kind = "idle"
        self.paint_count = 0
        self.detail_updates = 0
        self.paint_times = deque(maxlen=256)
        self.minimap_photo = None
        self.last_read_ms = 0.0
        self.requested_serial = 0
        self.initial_fit = True
        self.closed = False
        self.clear_cache_target: int | None = None
        self.worker = RenderWorker(image_path, (index.width, index.height), args.cache_mb, args.gdal_cache_mb, args.max_render_pixels)
        self._build_ui()
        self.worker.start()
        self.poll_after = root.after(30, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.close)
        saved = self.preferences.last_view if self.preferences.remember_view and not args.tile else None
        initial_tile = args.tile or (self.preferences.last_tile if self.preferences.remember_view else "")
        if saved and saved["map_key"] == self.map_key and saved["extent"] == [index.width, index.height]:
            self.startup_after = root.after(200, lambda: self._restore_startup(ViewState.from_dict(saved["state"])))
        elif initial_tile and index.find(initial_tile):
            self.startup_after = root.after(200, lambda: self._startup_tile(initial_tile))

    def _startup_tile(self, text: str) -> None:
        self.startup_after = None
        if not self.canvas.winfo_ismapped() or min(self.dimensions()) < 20:
            self.startup_after = self.root.after(50, lambda: self._startup_tile(text))
            return
        self.locate_text(text)

    def capture_view(self) -> ViewState:
        return ViewState(self.camera.cx, self.camera.cy, self.camera.scale,
                         self.selected.filename if self.selected else "", self.browser.row, self.query.get()[:512])

    def _begin_navigation(self) -> None:
        if not self.navigation_active:
            self.history.record(self.capture_view())
            self.navigation_active = True

    def _commit_navigation(self) -> None:
        if self.navigation_active:
            self.history.record(self.capture_view())
            self.navigation_active = False
            self._update_menus()

    def _restore_startup(self, state: ViewState) -> None:
        self.startup_after = None
        if not self.canvas.winfo_ismapped() or min(self.dimensions()) < 20:
            self.startup_after = self.root.after(50, lambda: self._restore_startup(state))
            return
        self.restore_view(state)
        self.history.reset(self.capture_view())
        self._update_menus()

    def restore_view(self, state: ViewState) -> None:
        if self.settle_after is not None:
            self.root.after_cancel(self.settle_after)
            self.settle_after = None
        self.navigation_active = False
        self.initial_fit = False
        self.selected = self.index.find(state.selected) if state.selected else None
        self.browser.row = state.row if state.row in self.index.rows else None
        self.row_combo.current(0 if self.browser.row is None else self.index.rows.index(self.browser.row) + 1)
        self.query.set(state.query)
        self._flush_filter()
        self._update_tile_details()
        self.camera.cx, self.camera.cy, self.camera.scale = state.cx, state.cy, state.scale
        self._mark_interaction()
        self._update_menus()
        self.request_render(0)

    def navigate_history(self, direction: int) -> str:
        self._commit_navigation()
        state = self.history.step(direction)
        if state is not None:
            self.restore_view(state)
        return "break"

    def dimensions(self) -> tuple[int, int]:
        return max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())

    def _query_changed(self, *_args) -> None:
        self.search_error_key = None
        self.search_error.set("")
        self.error_label.pack_forget()
        if self.filter_after is not None:
            self.root.after_cancel(self.filter_after)
        self.filter_after = self.root.after(100, self._filter)

    def _filter(self) -> None:
        self.filter_after = None
        self.filtered = self.browser.filter(self.query.get(), self.browser.row)
        self.listbox.delete(0, "end")
        if self.filtered:
            self.listbox.insert("end", *(tile.identifier for tile in self.filtered))
        self.match_text.set(self.tr("matches", count=len(self.filtered)))
        self._sync_browser_selection()

    def _sync_browser_selection(self) -> None:
        position = self.browser.position(self.selected)
        self.listbox.selection_clear(0, "end")
        if position is not None:
            self.listbox.selection_set(position)
            self.listbox.activate(position)
            self.listbox.see(position)
        self.browse_position.set(f"{position + 1 if position is not None else '—'} / {len(self.filtered):,}")
        for button, direction in ((self.previous_button, -1), (self.next_button, 1)):
            button.state(["!disabled"] if self.browser.neighbor(self.selected, direction) is not None else ["disabled"])
        if self.filtered:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=.5, rely=.35, anchor="center")

    def _row_changed(self, _event=None) -> None:
        position = self.row_combo.current()
        self.browser.row = None if position == 0 else self.index.rows[position - 1]
        self.query.set("")
        self._flush_filter()

    def _flush_filter(self) -> None:
        if self.filter_after is not None:
            self.root.after_cancel(self.filter_after)
            self.filter_after = None
        self._filter()

    def browse_step(self, direction: int) -> str:
        self._flush_filter_if_pending()
        tile = self.browser.neighbor(self.selected, direction)
        if tile is not None:
            self.select_tile(tile)
        return "break"

    def _flush_filter_if_pending(self) -> None:
        if self.filter_after is not None:
            self._flush_filter()

    def _focus_list(self, _event=None) -> str:
        self._flush_filter_if_pending()
        if self.filtered:
            self.listbox.focus_set()
            position = self.browser.position(self.selected)
            position = 0 if position is None else position
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(position)
            self.listbox.activate(position)
            self.listbox.see(position)
        return "break"

    def _locate_list(self, _event=None) -> str:
        selected = self.listbox.curselection()
        if selected:
            self.select_tile(self.filtered[selected[0]])
        return "break"

    def _click_list(self, _event=None) -> None:
        # Let Tk's release binding run too, so list drag/auto-scroll timers stop.
        self._locate_list()

    def locate_text(self, text: str) -> None:
        if not text.strip() and self.selected is not None:
            self.locate_selected()
            return
        tile = self.index.find(text)
        if tile is None:
            matches = self.index.suggestions(text)
            if text.strip() and len(matches) == 1:
                tile = matches[0]
            else:
                key = "empty_query" if not text.strip() else ("not_found" if not matches else "ambiguous")
                self._set_status(key)
                self.search_error_key = key
                self.search_error.set(self.tr(key))
                self.error_label.pack(fill="x", after=self.error_anchor, pady=(6, 0))
                return
        # A successful lookup opens the tile's row, leaving its neighbors available.
        self._commit_navigation()
        self._begin_navigation()
        self._browse_tile_row(tile)
        self.select_tile(tile, continue_action=True)

    def _browse_tile_row(self, tile: Tile) -> None:
        self.browser.row = tile.row
        self.row_combo.current(self.index.rows.index(tile.row) + 1)
        self.query.set("")
        self._flush_filter()

    def select_tile(self, tile: Tile, *, continue_action: bool = False) -> None:
        if not continue_action:
            self._commit_navigation()
        self._begin_navigation()
        if self.startup_after is not None:
            self.root.after_cancel(self.startup_after)
            self.startup_after = None
        self.selected = tile
        self.search_error_key = None
        self.search_error.set("")
        self.error_label.pack_forget()
        self._sync_browser_selection()
        self._update_tile_details()
        self._schedule_save()
        self._update_menus()
        self._fit_selected()

    def _update_tile_details(self) -> None:
        tile = self.selected
        if tile is None:
            self.tile_name.set(self.tr("no_selection"))
            for value in self.detail_fields.values():
                value.set("—")
            self.preferences.last_tile = ""
            return
        cx, cy = tile.center
        self.tile_name.set(tile.filename)
        values = {"grid": f"{tile.row:02} / {tile.column:02}", "origin": f"{tile.x:,}, {tile.y:,}",
                  "size": f"{tile.width:,} × {tile.height:,} px", "center": f"{cx:,.0f}, {cy:,.0f}"}
        for key, value in values.items():
            self.detail_fields[key].set(value)
        self.preferences.last_tile = tile.filename

    def _populate_map_menu(self, x: float, y: float) -> list[Tile]:
        tiles = self.index.at_point(x, y)
        self.tile_menu.delete(0, "end")
        self.tile_menu.add_command(label=self.tr("tiles_here", count=len(tiles)) if tiles else self.tr("no_tiles_here"), state="disabled")
        if tiles:
            self.tile_menu.add_separator()
        for tile in tiles:
            self.tile_menu.add_command(label=tile.identifier, command=lambda tile=tile: self.select_map_tile(tile))
        return tiles

    def select_map_tile(self, tile: Tile) -> None:
        self._commit_navigation()
        self._begin_navigation()
        self._browse_tile_row(tile)
        self.select_tile(tile, continue_action=True)

    def _map_context_menu(self, event) -> str:
        width, height = self.dimensions()
        keyboard = getattr(event, "keysym", "") == "F10"
        x, y = (width / 2, height / 2) if keyboard else (event.x, event.y)
        self._populate_map_menu(*self.camera.world_at(x, y, width, height))
        try:
            self.tile_menu.tk_popup(self.canvas.winfo_rootx() + round(x), self.canvas.winfo_rooty() + round(y))
        finally:
            self.tile_menu.grab_release()
        return "break"

    def locate_selected(self) -> None:
        if self.selected is None:
            self._set_status("select_first")
            return
        self._commit_navigation()
        self._begin_navigation()
        self._fit_selected()

    def _fit_selected(self) -> None:
        self.initial_fit = False
        self.camera.locate(self.selected, *self.dimensions())
        self._mark_interaction()
        self.request_render(0)

    def fit_all(self) -> None:
        self._commit_navigation()
        self._begin_navigation()
        self.initial_fit = False
        width, height = self.dimensions()
        self.camera.fit(self.index.width / 2, self.index.height / 2, self.index.width * 1.04, self.index.height * 1.04, width, height)
        self._mark_interaction()
        self.request_render(0)

    def actual_size(self) -> None:
        self._commit_navigation()
        self._begin_navigation()
        self.camera.scale = 1.0
        self._mark_interaction()
        self.request_render(0)

    def clear_selection(self) -> None:
        self.selected = None
        self.canvas.delete("selection")
        self.tile_name.set(self.tr("no_selection"))
        for value in self.detail_fields.values():
            value.set("—")
        self.preferences.last_tile = ""
        self._schedule_save()
        self._update_menus()
        self._sync_browser_selection()
        self._update_minimap()

    def clear_cache(self) -> None:
        self.clear_cache_target = self.worker.stats()["clears"] + 1
        self.worker.clear_cache()
        self._set_status("clearing")
        self._wake_poll()

    def _resize(self, _event=None) -> None:
        if min(self.dimensions()) < 20:
            return
        width, height = self.dimensions()
        self.canvas.coords(self.loading_item, width / 2, height / 2)
        self._place_minimap()
        if self.initial_fit:
            self.fit_all()
            self.history.reset(self.capture_view())
            self.navigation_active = False
            self._update_menus()
        else:
            self.request_render(50)

    def request_render(self, delay: int = 40) -> None:
        self._request_paint()
        # Throttle instead of endlessly restarting a debounce timer: data keeps
        # arriving during an uninterrupted gesture, not only after it stops.
        if delay == 0 and self.render_after is not None:
            self.root.after_cancel(self.render_after)
            self.render_after = None
        if self.render_after is None:
            self.render_after = self.root.after(delay, self._submit)

    def _submit(self) -> None:
        self.render_after = None
        width, height = self.dimensions()
        previous = self.frame_request
        # The retained detail already has a 128 px read-ahead border. Reuse it
        # until only 32 px remain, instead of decoding the same overlap per event.
        if (previous is not None and self.detail_frame is not None
                and previous.serial == self.requested_serial
                and math.isclose(previous.scale, self.camera.scale, rel_tol=1e-9)
                and (previous.width, previous.height) == (width + 2 * OVERSCAN, height + 2 * OVERSCAN)
                and self.detail_frame.contains(self.camera, width + 64, height + 64)):
            self.reused_views += 1
            return
        self.requested_serial = self.worker.submit(self.camera, width, height, padding=OVERSCAN)
        self._wake_poll()

    def _wake_poll(self) -> None:
        if self.poll_after is not None:
            self.root.after_cancel(self.poll_after)
        self.poll_after = self.root.after(25, self._poll)

    def _request_paint(self) -> None:
        if not self.closed and self.paint_after is None:
            self.paint_after = self.root.after(16, self._paint)

    def _paint(self) -> None:
        self.paint_after = None
        if self.closed or (self.overview is None and self.detail_frame is None):
            return
        started = time.perf_counter()
        width, height = self.dimensions()
        interactive = (self.drag_position is not None or time.monotonic() - self.last_interaction < 0.14
                       or self.frame_request is None or self.frame_request.serial != self.requested_serial)
        # A zoom resizes every texture anyway; avoid uploading an unused border
        # for each wheel step. Pans and settled views retain their read-ahead.
        padding = OVERSCAN if not interactive or self.drag_position is not None or self.interaction_kind == "pan" else 0
        key = self.detail_updates, self.overview is not None, width, height, self.camera.scale, self.background, interactive, padding
        display = self.display_request
        reusable = (self.photo is not None and key == self.display_key and display is not None
                    and abs((display.cx - self.camera.cx) * self.camera.scale) <= (display.width - width) / 2
                    and abs((display.cy - self.camera.cy) * self.camera.scale) <= (display.height - height) / 2)
        if reusable:
            self.translated_paints += 1
        else:
            display = RenderRequest(0, self.camera.cx, self.camera.cy, self.camera.scale, width + 2 * padding, height + 2 * padding)
            image = compose_view(self.overview, self.detail_frame, self.camera, display.width, display.height,
                                 interactive=interactive, background=self.background, opaque=True)
            photo = ImageTk.PhotoImage(image, master=self.root)
            if self.image_item is None:
                self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=photo, tags="map")
            else:
                self.canvas.itemconfigure(self.image_item, image=photo, state="normal")
            self.photo, self.display_request, self.display_key = photo, display, key
            self.texture_uploads += 1
        self.canvas.coords(self.image_item, (display.cx - self.camera.cx) * self.camera.scale + (width - display.width) / 2,
                           (display.cy - self.camera.cy) * self.camera.scale + (height - display.height) / 2)
        self.canvas.delete("loading")
        self.canvas.tag_lower("map")
        self.draw_selection()
        self._update_minimap()
        self.scale_text.set(f"{self.camera.scale * 100:.1f}%")
        self.paint_count += 1
        self.paint_times.append((time.perf_counter() - started) * 1000)

    def _poll(self) -> None:
        self.poll_after = None
        if self.closed:
            return
        if self.worker.fatal_error:
            messagebox.showerror(self.tr("map_error"), self.worker.fatal_error, parent=self.root)
            self.close()
            return
        overview = self.worker.get_overview()
        if self.overview is None and overview is not None:
            self.overview = overview
            self._build_minimap()
            self._request_paint()
        result = self.worker.take_result()
        if result is not None:
            if result.error:
                if result.request.serial == self.requested_serial:
                    self._set_status("detail_error", error=result.error)
            elif result.image is not None:
                detail = WorldFrame.from_result(result)
                if detail.intersects(self.camera, *self.dimensions()):
                    self.detail_frame = detail
                    self.frame_request = result.request
                    self.last_read_ms = result.milliseconds
                    self.detail_updates += 1
                    self._request_paint()
                    self._set_status("ready")
        stats = self.worker.stats()
        if self.clear_cache_target is not None and stats["clears"] >= self.clear_cache_target:
            self.clear_cache_target = None
            if result is None:
                self._set_status("cleared")
        self._check_system_theme()
        idle = (self.frame_request is not None and self.frame_request.serial == self.requested_serial
                and not stats["pending"] and self.clear_cache_target is None)
        self.poll_after = self.root.after(125 if idle else 25, self._poll)

    def _position_frame(self) -> None:
        self._request_paint()

    def _build_minimap(self) -> None:
        image = self.overview.image.copy()
        image.thumbnail((152, 176), Image.Resampling.LANCZOS)
        self.minimap_photo = ImageTk.PhotoImage(image, master=self.root)
        x, y = (152 - image.width) / 2, (176 - image.height) / 2
        self.minimap.create_image(x, y, anchor="nw", image=self.minimap_photo, tags="overview")
        self.minimap_mapping = x, y, image.width / self.index.width, image.height / self.index.height

    def _update_minimap(self) -> None:
        if self.minimap_photo is None or not self.minimap_frame.winfo_manager():
            return
        self.minimap.delete("viewport")
        x, y, sx, sy = self.minimap_mapping
        width, height = self.dimensions()
        left, top = self.camera.world_at(0, 0, width, height)
        right, bottom = self.camera.world_at(width, height, width, height)
        self.minimap.create_rectangle(x + left * sx, y + top * sy, x + right * sx, y + bottom * sy,
                                      outline=self.colors["accent"], width=2, tags="viewport")
        if self.selected:
            cx, cy = self.selected.center
            cx, cy = x + cx * sx, y + cy * sy
            self.minimap.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=self.colors["locator"], outline=self.colors["halo"], tags="viewport")

    def _minimap_click(self, event) -> None:
        if self.minimap_photo is None:
            return
        self._commit_navigation()
        self._begin_navigation()
        x, y, sx, sy = self.minimap_mapping
        self.camera.cx = min(self.index.width, max(0, (event.x - x) / sx))
        self.camera.cy = min(self.index.height, max(0, (event.y - y) / sy))
        self._mark_interaction()
        self.request_render(0)

    def draw_selection(self) -> None:
        self.canvas.delete("selection")
        tile = self.selected
        if tile is None:
            return
        width, height = self.dimensions()
        x0, y0 = self.camera.screen_at(tile.x, tile.y, width, height)
        x1, y1 = self.camera.screen_at(tile.x + tile.width, tile.y + tile.height, width, height)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=self.colors["halo"], width=5, tags="selection")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=self.colors["locator"], width=2, tags="selection")
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.canvas.create_line(cx - 7, cy, cx + 7, cy, fill=self.colors["locator"], width=2, tags="selection")
        self.canvas.create_line(cx, cy - 7, cx, cy + 7, fill=self.colors["locator"], width=2, tags="selection")
        label_x, label_y = max(4, min(width - 265, x0)), max(4, min(height - 25, y0 - 24))
        label = self.canvas.create_text(label_x + 6, label_y + 4, anchor="nw", text=tile.filename, fill="white", font=("Segoe UI", 9), tags="selection")
        bounds = self.canvas.bbox(label)
        if bounds:
            box = self.canvas.create_rectangle(bounds[0] - 7, bounds[1] - 4, bounds[2] + 7, bounds[3] + 4, fill=self.colors["halo"], outline="", tags="selection")
            self.canvas.tag_lower(box, label)

    def _drag_start(self, event) -> None:
        self._commit_navigation()
        self._begin_navigation()
        self.canvas.focus_set()
        self.drag_position = (event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _drag(self, event) -> None:
        if self.drag_position is None:
            return
        x, y = self.drag_position
        self.camera.cx -= (event.x - x) / self.camera.scale
        self.camera.cy -= (event.y - y) / self.camera.scale
        self.drag_position = (event.x, event.y)
        self._mark_interaction("pan")
        self.request_render(40)

    def _drag_end(self, _event=None) -> None:
        self.drag_position = None
        self.canvas.configure(cursor="crosshair")
        self.last_interaction = 0
        self._commit_navigation()
        self.request_render(0)

    def _mouse_position(self, event) -> None:
        if not self.preferences.show_coordinates:
            return
        x, y = self.camera.world_at(event.x, event.y, *self.dimensions())
        self.position.set(f"X {x:,.0f}　Y {y:,.0f}")

    def _wheel(self, event) -> str:
        steps = max(-3.0, min(3.0, event.delta / 120))
        self._zoom(1.2 ** steps, event.x, event.y)
        return "break"

    def zoom_center(self, factor: float) -> None:
        width, height = self.dimensions()
        self._zoom(factor, width / 2, height / 2)

    def _zoom(self, factor: float, x: float, y: float) -> None:
        self._begin_navigation()
        width, height = self.dimensions()
        minimum = min(width / self.index.width, height / self.index.height) / 5
        self.camera.zoom(factor, x, y, width, height, minimum)
        self._mark_interaction("zoom")
        self.request_render(40)

    def pan_by(self, dx: float, dy: float) -> None:
        self._begin_navigation()
        self.camera.cx += dx / self.camera.scale
        self.camera.cy += dy / self.camera.scale
        self._mark_interaction("pan")
        self.request_render(40)

    def _mark_interaction(self, kind: str = "jump") -> None:
        self.interaction_kind = kind
        self.last_interaction = time.monotonic()
        if self.settle_after is not None:
            self.root.after_cancel(self.settle_after)
        self.settle_after = self.root.after(170, self._finish_interaction)

    def _finish_interaction(self) -> None:
        self.settle_after = None
        if self.drag_position is None:
            self._commit_navigation()
        self._request_paint()

    def close(self) -> None:
        if self.closed:
            return
        for name in ("render_after", "filter_after", "paint_after", "settle_after", "poll_after", "startup_after"):
            timer = getattr(self, name)
            if timer is not None:
                self.root.after_cancel(timer)
                setattr(self, name, None)
        if self.save_after is not None:
            self.root.after_cancel(self.save_after)
            self.save_after = None
        if self.preferences.remember_view:
            width, height = self.normal_size if self.fullscreen else (self.root.winfo_width(), self.root.winfo_height())
            self.preferences.window_width = max(960, width)
            self.preferences.window_height = max(640, height)
            self.preferences.last_tile = self.selected.filename if self.selected else ""
            self.preferences.last_view = dict(state=self.capture_view().to_dict(), map_key=self.map_key,
                                              extent=[self.index.width, self.index.height])
        self._save_preferences()
        self.close_settings()
        self.close_info()
        self.closed = True
        self.worker.stop()
        self.root.withdraw()
        self.worker.join(timeout=3)
        self.root.destroy()

