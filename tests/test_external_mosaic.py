"""An orthomosaic is registered without rebuilding its display PNGs."""
from pathlib import Path
import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_origin

from aerial_atlas.mosaic import build, current_map, preflight, Progress, BuildCancelled
from aerial_atlas.mosaic_viewer import TileIndex


class ExternalMapTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.project = Path(self.folder.name).resolve()
        self.source = self.project / "images/1150618/tiles_png_1024"
        self.source.mkdir(parents=True)
        for column, color in ((0, "red"), (1, "blue")):
            Image.new("RGB", (1024, 1024), color).save(self.source / f"1150618_tile_0_{column}.png")
        self.image = self.source.parent / "1150618_input_orthomosaic-001.tif"
        with rasterio.open(self.image, "w", driver="GTiff", width=1536, height=1280,
                           count=8, dtype="uint16", crs="EPSG:3826",
                           transform=from_origin(305000, 2785000, .03, .03),
                           tiled=True, blockxsize=256, blockysize=256, compress="deflate") as dataset:
            pixels = np.full((8, 1280, 1536), 32768, dtype="uint16")
            pixels[7] = 65535
            pixels[7, :256] = 0
            dataset.write(pixels)
        self.output = self.project / "generated/1150618"

    def prepare(self, progress=None):
        return build(self.source, self.output, progress, index_base=0, image=self.image)

    def test_registration_keeps_source_names_and_full_raster_extent(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        report = self.prepare()
        info = current_map(self.output, self.source)
        self.assertEqual(info["image"], self.image)
        self.assertFalse(info["changed"])
        self.assertEqual(report["source_count"], 2)
        self.assertIsNone(report["overlap_validation"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        self.assertFalse(list(self.output.rglob("*.tif")))
        index = TileIndex(info["index"], extent=(1536, 1280))
        self.assertEqual((index.width, index.height), (1536, 1280))
        self.assertEqual(index.find("0_1").x, 512)
        self.assertLess(preflight(self.source, self.output, index_base=0, image=self.image)["required_bytes"], 5 * 1024**2)

    def test_relative_reference_survives_moving_the_project(self):
        self.prepare()
        moved = self.project / "relocated"
        shutil.copytree(self.project / "images", moved / "images")
        shutil.copytree(self.project / "generated", moved / "generated")
        info = current_map(moved / "generated/1150618")
        self.assertEqual(info["image"], moved / "images/1150618" / self.image.name)

    def test_changed_tiff_is_detected_without_rebuilding_it(self):
        self.prepare()
        modified = self.image.stat().st_mtime_ns + 1_000_000_000
        os.utime(self.image, ns=(modified, modified))
        self.assertTrue(current_map(self.output, self.source)["changed"])

    def test_cancelled_registration_preserves_current_index(self):
        self.prepare()
        before = (self.output / "current.json").read_bytes()
        cancel = self.project / "cancel"

        def callback(event):
            if event["stage"] == "verifying":
                cancel.touch()

        with self.assertRaises(BuildCancelled):
            self.prepare(Progress(cancel=cancel, callback=callback))
        self.assertEqual((self.output / "current.json").read_bytes(), before)
        self.assertFalse(list(self.output.glob(".staging-*")))

    def test_tile_outside_tiff_is_rejected_before_publication(self):
        Image.new("RGB", (1024, 1024)).save(self.source / "1150618_tile_0_2.png")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.prepare()
        self.assertFalse((self.output / "current.json").exists())
