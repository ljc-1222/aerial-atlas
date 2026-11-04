"""The normal headless entry point prepares downloads without manual commands."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from aerial_atlas.app import main
from aerial_atlas.mosaic import current_map
from support import survey_downloads


class DownloadEntryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="atlas entry ")
        self.addCleanup(self.folder.cleanup)
        self.project = Path(self.folder.name).resolve()

    def run_app(self, *options):
        with patch("aerial_atlas.app.configure_logging", return_value=self.project / "logs"), contextlib.redirect_stdout(io.StringIO()):
            return main(["--project-dir", str(self.project), "--settings-path", str(self.project / "settings.json"),
                         "--build", *options])

    def test_each_new_survey_is_imported_and_registered_from_original_downloads(self):
        for id in ("1150618", "1150806"):
            with self.subTest(id=id):
                image = survey_downloads(self.project, id)
                before = image.read_bytes()
                self.assertEqual(self.run_app("--dataset", id), 0)
                info = current_map(self.project / "generated" / id)
                self.assertEqual(info["image"], image)
                self.assertEqual(image.read_bytes(), before)
                self.assertEqual(info["manifest"]["source_count"], 2)
                self.assertFalse(list((self.project / "generated" / id).rglob("*.tif")))
                self.assertEqual(info["manifest"]["extent"]["height"], 1280)

    def test_first_downloaded_survey_is_selected_when_april_is_absent(self):
        survey_downloads(self.project)
        self.assertEqual(self.run_app(), 0)
        self.assertTrue((self.project / "generated/1150618/current.json").is_file())
        self.assertFalse((self.project / "generated/1150427/current.json").exists())

    def test_missing_source_tiff_does_not_publish_a_png_mosaic(self):
        survey_downloads(self.project).unlink()
        progress = self.project / "progress.json"
        with self.assertLogs(level="ERROR"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_app("--dataset", "1150618", "--progress-file", str(progress)), 1)
        self.assertIn("orthomosaic", json.loads(progress.read_text())["message"])
        self.assertFalse(list(self.project.glob("generated/*/current.json")))

    def test_conflicting_layout_is_reported_through_normal_failure_record(self):
        for folder in ("RGB_png", "tiles_png_1024"):
            target = self.project / "images/1150427" / folder
            target.mkdir(parents=True)
            (target / "20260427_nodata_01_01.png").write_bytes(folder.encode())
        progress = self.project / "progress.json"
        with self.assertLogs(level="ERROR"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_app("--dataset", "1150427", "--progress-file", str(progress)), 1)
        failure = json.loads(progress.read_text())
        self.assertEqual(failure["stage"], "failed")
        self.assertIn("Both RGB_png", failure["message"])

    def test_april_directory_is_adapted_without_renaming_or_padding_rgb(self):
        old = self.project / "images/1150427/RGB_png"
        old.mkdir(parents=True)
        name = "20260427_nodata_01_01.png"
        Image.new("RGB", (810, 1024), "green").save(old / name)
        before = (old / name).read_bytes()
        self.assertEqual(self.run_app("--dataset", "1150427"), 0)
        new = self.project / "images/1150427/tiles_png_1024" / name
        self.assertEqual(new.read_bytes(), before)
        info = current_map(self.project / "generated/1150427")
        self.assertEqual(info["manifest"]["extent"]["width"], 810)
