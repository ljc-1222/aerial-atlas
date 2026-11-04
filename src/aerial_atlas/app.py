"""GUI entry point and headless reconstruction command."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from . import __version__
from .paths import default_project, source_dir, output_dir, atomic_json
from .datasets import DATASET_IDS, find_dataset, prepare_downloads, resolve_data_root


def configure_logging(worker=False):
    directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config")) / "UAVMapViewer" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / ("build.log" if worker else "app.log"),
                                  maxBytes=1024*1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return directory


def parser():
    result = argparse.ArgumentParser(description="Aerial Atlas: local tiles and world map")
    result.add_argument("--version", action="version", version=f"Aerial Atlas {__version__}")
    result.add_argument("--project-dir", type=Path, default=default_project(), help="Root containing images/ and generated/")
    result.add_argument("--dataset", choices=DATASET_IDS, help="Survey to open (defaults to the last selection)")
    result.add_argument("--data-dir", type=Path, help="Folder containing downloaded survey files or ZIPs")
    result.add_argument("--source", type=Path, help="Override the source tile directory")
    result.add_argument("--build", action="store_true", help="Create and verify the map without opening the GUI")
    result.add_argument("--demo", action="store_true", help="Create synthetic example tiles (never overwrites existing sources)")
    result.add_argument("--tile", help="Initial filename or row_column to locate")
    result.add_argument("--image", type=Path, help="Open a specific TIFF together with --index")
    result.add_argument("--index", type=Path, help="Tile CSV for --image")
    result.add_argument("--cache-mb", type=int, default=64)
    result.add_argument("--gdal-cache-mb", type=int, default=64)
    result.add_argument("--max-render-pixels", type=int, default=2_000_000)
    result.add_argument("--settings-path", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--progress-file", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--cancel-file", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    return result


def self_test(args, output):
    import tkinter as tk
    from .mosaic import current_map
    from .mosaic_viewer import TileIndex, RenderRequest, render_region
    import rasterio
    info = current_map(output)
    extent = info["manifest"]["extent"]
    index = TileIndex(info["index"], extent=(extent["width"], extent["height"]))
    tile = index.find(args.tile) if args.tile else index.tiles[0]
    if tile is None:
        raise ValueError("Requested self-test tile was not found")
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    with rasterio.open(info["image"]) as dataset:
        image = render_region(dataset, RenderRequest(1, *tile.center, 1, tile.width, tile.height), 2_000_000)
        report = dict(status="passed", frozen=bool(getattr(sys, "frozen", False)), tkinter=True,
                      tiles=len(index.tiles), extent=[index.width, index.height], tile=tile.identifier,
                      overviews=dataset.overviews(1), reference_rgb_sha256=hashlib.sha256(image.convert("RGB").tobytes()).hexdigest())
    atomic_json(args.self_test_report, report)


def main(argv=None):
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if bool(args.image) != bool(args.index):
        argument_parser.error("--image and --index must be provided together")
    if not (0 <= args.cache_mb <= 512 and 1 <= args.gdal_cache_mb <= 512
            and 10_000 <= args.max_render_pixels <= 8_000_000):
        argument_parser.error("Cache limits must be 0..512 / 1..512 MiB; render pixels 10000..8000000")
    args.project_dir = args.project_dir.resolve()
    from .viewer_preferences import SettingsStore
    requested_dataset = args.dataset
    args.dataset_explicit = bool(requested_dataset or args.source or args.image)
    args.dataset = args.dataset or ("1150427" if args.source or args.demo else SettingsStore(args.settings_path).load().last_dataset)
    args.data_dir = resolve_data_root(args.project_dir, args.data_dir)
    source = (args.source or args.data_dir / args.dataset / "tiles_png_1024").resolve()
    output = output_dir(args.project_dir, args.dataset)
    # Apply before NumPy/GDAL import, including in headless build subprocesses.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    try:
        log_dir = configure_logging(args.build)
        if args.demo:
            from .demo import create_demo
            source = (args.source or source_dir(args.project_dir, args.dataset, args.data_dir)).resolve()
            create_demo(source)
        if args.build:
            from .mosaic import build, Progress, BuildCancelled
            progress = Progress(args.progress_file, args.cancel_file)
            try:
                prepare_downloads(args.data_dir, progress)
                dataset = find_dataset(args.data_dir, args.dataset)
                if not requested_dataset and not args.source and not dataset.tiles.is_dir():
                    for id in DATASET_IDS:
                        candidate = find_dataset(args.data_dir, id)
                        if candidate.tiles.is_dir():
                            dataset = candidate
                            args.dataset = id
                            break
                source = (args.source or dataset.tiles).resolve()
                output = output_dir(args.project_dir, args.dataset)
                if dataset.index_base == 0 and dataset.image is None:
                    raise FileNotFoundError(f"Download {dataset.id}_input_orthomosaic-001.tif together with its tile ZIPs.")
                report = build(source, output, progress, index_base=dataset.index_base, image=dataset.image)
                if sys.stdout is not None:
                    print(f"Created {report['source_count']} tiles in {report['elapsed_seconds']:.1f}s")
                return 0
            except BuildCancelled as error:
                if args.progress_file:
                    atomic_json(args.progress_file, dict(stage="cancelled", message=str(error), done=0, total=0))
                return 2
        if args.self_test_report:
            self_test(args, output)
            return 0
        if os.name == "nt":
            try:
                ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                pass
        import tkinter as tk
        from tkinter import messagebox
        from .startup import Startup
        root = tk.Tk()

        def report_exception(kind, value, trace):
            logging.getLogger(__name__).error("UI callback failed", exc_info=(kind, value, trace))
            messagebox.showerror("Aerial Atlas", f"{value}\n\nDetails are saved in:\n{log_dir}", parent=root)

        root.report_callback_exception = report_exception
        Startup(root, args, source, output, log_dir)
        root.mainloop()
        return 0
    except Exception as error:
        logging.exception("Aerial Atlas operation failed")
        message = f"{error}"
        if args.build:
            if args.progress_file:
                atomic_json(args.progress_file, dict(stage="failed", message=message, done=0, total=0))
        elif args.self_test_report:
            atomic_json(args.self_test_report, dict(status="failed", error=message))
        else:
            import tkinter as tk
            from tkinter import messagebox
            failure_root = tk.Tk()
            failure_root.withdraw()
            messagebox.showerror("Aerial Atlas", message, parent=failure_root)
            failure_root.destroy()
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1
