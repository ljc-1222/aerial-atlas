from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import rasterio
from aerial_atlas.demo import create_demo
from aerial_atlas.mosaic import (build, build_lock, current_map, preflight, Progress, BuildCancelled, source_snapshot)
from aerial_atlas.paths import atomic_json
from aerial_atlas.reconstruction import inventory
from support import fixture


class ReconstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def test_every_source_matches_full_rgb_and_valid_black_mask(self):
        data = self.data
        self.assertEqual(data["report"]["verification"]["tiles_checked"], 14)
        with rasterio.open(data["image"]) as dataset:
            for tile in inventory(data["source"], 512):
                with Image.open(tile.path) as image:
                    np.testing.assert_array_equal(dataset.read(window=tile.window).transpose(1, 2, 0), np.asarray(image))
                self.assertTrue(np.all(dataset.dataset_mask(window=tile.window) == 255))
            self.assertEqual(dataset.dataset_mask(window=rasterio.windows.Window(0, 0, 1, 1))[0, 0], 0)
            self.assertTrue(np.all(dataset.read(window=rasterio.windows.Window(720, 720, 1, 1)) == 0))
            self.assertIsNone(dataset.crs)
            self.assertIsNone(dataset.nodata)
        self.assertLessEqual(data["report"]["overlap_validation"]["peak_source_cache_bytes"], 64*1024**2)

    def test_startup_without_original_pngs_remains_valid(self):
        self.assertFalse(current_map(self.data["output"], Path("no such source"))["changed"])
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(current_map(self.data["output"],Path(folder))["changed"])

    def test_interrupted_staging_is_cleaned_under_lock(self):
        output=self.data["output"]
        stale=output/(".staging-"+"0"*32)
        stale.mkdir()
        (stale/"partial.tif").write_bytes(b"interrupted")
        with tempfile.TemporaryDirectory() as folder:
            cancel=Path(folder)/"cancel"
            cancel.touch()
            with self.assertRaises(BuildCancelled):
                build(self.data["source"],output,Progress(cancel=cancel))
        self.assertFalse(stale.exists())
        self.assertFalse(current_map(output)["changed"])

    def test_fingerprint_detects_changed_sources_without_decoding_images(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            (source / "unrelated.txt").write_text("ignored")
            self.assertEqual(source_snapshot(source), [])
            (source / "x.png").write_bytes(b"not an image")
            self.assertEqual(len(source_snapshot(source)), 1)

    def test_filename_conflicts_and_bad_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            create_demo(source)
            original = next(source.glob("*.png"))
            (source / "duplicate_01_02.png").write_bytes((source / "demo_01_02.png").read_bytes())
            with self.assertRaisesRegex(ValueError, "duplicate"):
                inventory(source, 512)
            (source / "duplicate_01_02.png").unlink()
            Image.new("RGB", (512, 512)).save(original)
            with self.assertRaisesRegex(ValueError, "dimensions"):
                inventory(source, 512)

    def test_conflicting_pixels_never_publish_a_map(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source", Path(folder)/"output"
            create_demo(source)
            path = source / "demo_01_02.png"
            with Image.open(path) as image:
                pixels = np.array(image)
            pixels[600, 600] ^= 255
            Image.fromarray(pixels).save(path)
            with self.assertRaisesRegex(ValueError, "pixels disagree"):
                build(source, output)
            self.assertFalse((output / "current.json").exists())
            self.assertFalse(list(output.glob(".staging-*")))

    def test_cancel_and_enospc_preserve_previous_generation(self):
        data = self.data
        before = (data["output"] / "current.json").read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            cancel = Path(folder) / "cancel"
            def callback(event):
                if event["stage"] == "writing":
                    cancel.touch()
            with self.assertRaises(BuildCancelled):
                build(data["source"], data["output"], Progress(cancel=cancel, callback=callback))
        self.assertEqual((data["output"] / "current.json").read_bytes(), before)
        with patch("aerial_atlas.mosaic.build_mosaic", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                build(data["source"], data["output"])
        self.assertEqual((data["output"] / "current.json").read_bytes(), before)
        self.assertFalse(list(data["output"].glob(".staging-*")))
        self.assertFalse(current_map(data["output"])["changed"])

    def test_low_disk_estimate_disables_creation(self):
        with patch("aerial_atlas.mosaic.shutil.disk_usage") as disk:
            disk.return_value.free = 1
            self.assertFalse(preflight(self.data["source"], self.data["output"])["enough_space"])

    def test_os_lock_rejects_second_process_and_is_released(self):
        output = self.data["output"]
        code = "from pathlib import Path; from aerial_atlas.mosaic import build_lock; "
        code += f"\nwith build_lock(Path({str(output)!r})): pass\n"
        with build_lock(output):
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another Aerial Atlas", result.stderr)
        with build_lock(output):
            pass

    def test_untrusted_pointer_cannot_escape_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            atomic_json(output / "current.json", dict(schema=1, directory="../outside"))
            with self.assertRaisesRegex(ValueError, "Invalid map generation"):
                current_map(output)

    def test_incomplete_or_modified_index_is_rejected(self):
        data = self.data
        path = data["index"]
        original = path.read_bytes()
        try:
            path.write_bytes(original+b"broken\n")
            with self.assertRaisesRegex(ValueError, "incomplete or has changed"):
                current_map(data["output"])
        finally:
            path.write_bytes(original)

    def test_changed_source_prompts_rebuild_and_demo_cannot_overwrite(self):
        data = self.data
        added = data["source"] / "new_10_10.png"
        try:
            added.write_bytes(b"changed")
            self.assertTrue(current_map(data["output"], data["source"])["changed"])
        finally:
            added.unlink()
        with self.assertRaises(FileExistsError):
            create_demo(data["source"])
