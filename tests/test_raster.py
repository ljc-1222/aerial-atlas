import csv
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.windows import Window

from aerial_atlas.mosaic_viewer import Camera, RenderRequest, RenderWorker, TileIndex, render_region
from aerial_atlas.raster import inspect_raster, read_display, validate_raster
from aerial_atlas.reconstruction import inventory


class RasterTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryFile()
        self.addCleanup(self.memory.close)
        pixels = np.zeros((8, 3, 4), dtype=np.uint16)
        pixels[0] = [0, 32768, 65535, 257]
        pixels[1] = [0, 514, 1028, 65535]
        pixels[2] = [0, 65535, 257, 32768]
        pixels[7] = 65535
        pixels[7, 0, 0] = 0
        with self.memory.open(driver="GTiff", width=4, height=3, count=8, dtype="uint16",
                              crs="EPSG:3826", transform=from_origin(300000, 2800000, .03, .03)) as dataset:
            dataset.write(pixels)
        self.dataset = self.memory.open()
        self.addCleanup(self.dataset.close)

    def test_uint16_rgb_and_explicit_alpha_preserve_channel_order_and_valid_black(self):
        self.assertTrue(np.all(self.dataset.dataset_mask() == 255))
        image = read_display(self.dataset, Window(0, 0, 4, 3), (3, 4))
        self.assertEqual(tuple(image[1, 1]), (128, 2, 255, 255))
        self.assertEqual(tuple(image[1, 2]), (255, 4, 1, 255))
        self.assertEqual(tuple(image[1, 3]), (1, 255, 128, 255))
        self.assertEqual(tuple(image[0, 0]), (0, 0, 0, 0))
        self.assertEqual(tuple(image[1, 0]), (0, 0, 0, 255))

    def test_same_pixel_has_the_same_colour_in_different_view_windows(self):
        full = read_display(self.dataset, Window(0, 0, 4, 3), (3, 4))
        cropped = read_display(self.dataset, Window(1, 0, 2, 3), (3, 2))
        np.testing.assert_array_equal(cropped, full[:, 1:3])
        rendered = render_region(self.dataset, RenderRequest(1, 2, 1.5, 1, 4, 3), 12)
        np.testing.assert_array_equal(np.asarray(rendered), full)

    def test_metadata_keeps_full_raster_extent_and_georeferencing(self):
        metadata = inspect_raster(self.memory.name)
        self.assertEqual((metadata["width"], metadata["height"], metadata["count"]), (4, 3, 8))
        self.assertEqual(metadata["dtypes"], ["uint16"] * 8)
        self.assertEqual(metadata["crs"], "EPSG:3826")
        self.assertEqual(metadata["transform"][:3], [.03, 0, 300000])

    def test_worker_accepts_multispectral_source_and_keeps_bounded_rendering(self):
        worker = RenderWorker(self.memory.name, (4, 3), pixel_limit=12)
        worker.submit(Camera(2, 1.5, 1), 4, 3)
        worker.start()
        try:
            result = None
            deadline = time.monotonic() + 5
            while result is None and time.monotonic() < deadline:
                result = worker.take_result()
                time.sleep(.01)
            self.assertIsNotNone(result, worker.fatal_error)
            self.assertIsNone(result.error)
            self.assertLessEqual(result.image.width * result.image.height, 12)
        finally:
            worker.stop()
            worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_rgb8_mask_and_unsupported_formats(self):
        with MemoryFile() as memory:
            with memory.open(driver="GTiff", width=2, height=1, count=3, dtype="uint8") as dataset:
                dataset.write(np.array([[[0, 100]], [[0, 150]], [[0, 200]]], dtype=np.uint8))
                dataset.write_mask(np.array([[255, 0]], dtype=np.uint8))
                rgba = read_display(dataset, Window(0, 0, 2, 1), (1, 2))
                self.assertEqual(tuple(rgba[0, 0]), (0, 0, 0, 255))
                self.assertEqual(tuple(rgba[0, 1]), (100, 150, 200, 0))
        with MemoryFile() as memory:
            with memory.open(driver="GTiff", width=2, height=1, count=4, dtype="uint8") as dataset:
                with self.assertRaisesRegex(ValueError, "3-band uint8"):
                    validate_raster(dataset)


class GridTests(unittest.TestCase):
    def test_both_index_origins_keep_original_names_and_cropped_dimensions(self):
        for base, name in ((0, "survey_tile_0_1.png"), (1, "original_nodata_1_2.png")):
            with self.subTest(base=base), tempfile.TemporaryDirectory() as folder:
                source = Path(folder)
                path = source / name
                Image.new("RGB", (810, 1024)).save(path)
                before = path.read_bytes()
                tile, = inventory(source, 512, index_base=base)
                self.assertEqual((tile.path.name, tile.x, tile.y, tile.width), (name, 512, 0, 810))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((tile.row, tile.col), (base, base + 1))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            Image.new("RGB", (1024, 1024)).save(source / "survey_tile_0_0.png")
            with self.assertRaisesRegex(ValueError, "grid coordinate"):
                inventory(source, 512)
            with self.assertRaisesRegex(ValueError, "index base"):
                inventory(source, 512, index_base=2)

    def test_zero_origin_index_has_an_independent_full_raster_extent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tile_index.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["filename", "row", "column", "x", "y", "width", "height"])
                writer.writerow(["survey_tile_0_0.png", 0, 0, 0, 0, 1024, 1024])
            index = TileIndex(path, extent=(1100, 1200))
            self.assertEqual((index.width, index.height), (1100, 1200))
            self.assertEqual(index.find("0_0").filename, "survey_tile_0_0.png")
            self.assertEqual(index.at_point(1050, 1100), [])
            self.assertEqual((TileIndex(path).width, TileIndex(path).height), (1024, 1024))
            with self.assertRaisesRegex(ValueError, "outside"):
                TileIndex(path, extent=(1000, 1200))
            with self.assertRaisesRegex(ValueError, "extent"):
                TileIndex(path, extent=(0, 1200))
