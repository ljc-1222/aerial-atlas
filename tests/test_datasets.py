from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from aerial_atlas.datasets import (DATASET_IDS, find_dataset, prepare_downloads,
                                   resolve_data_root)


def download(root, members, name="download.zip"):
    path = root / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in members:
            archive.writestr(member, data)
    return path


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="atlas downloads ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_legacy_directory_keeps_original_names_bytes_and_annotations(self):
        source = self.root / "1150427/RGB_png"
        source.mkdir(parents=True)
        original = source / "20260427_nodata_36_42.png"
        original.write_bytes(b"unchanged image")
        annotation = self.root / "1150427/split/annotation.json"
        annotation.parent.mkdir()
        annotation.write_bytes(b'{"imagePath":"20260427_nodata_36_42.png"}')
        before = original.stat()
        prepare_downloads(self.root)
        dataset = find_dataset(self.root, "1150427")
        normalized = dataset.tiles / original.name
        self.assertEqual(dataset.index_base, 1)
        self.assertEqual(dataset.tiles.name, "tiles_png_1024")
        self.assertEqual(normalized.read_bytes(), b"unchanged image")
        self.assertEqual(normalized.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(annotation.read_bytes(), b'{"imagePath":"20260427_nodata_36_42.png"}')
        prepare_downloads(self.root)
        self.assertEqual(list(dataset.tiles.iterdir()), [normalized])

    def test_partial_canonical_directory_cannot_hide_legacy_tiles(self):
        legacy = self.root / "1150427/RGB_png"
        canonical = self.root / "1150427/tiles_png_1024"
        legacy.mkdir(parents=True)
        canonical.mkdir()
        (legacy / "20260427_nodata_01_29.png").write_bytes(b"first legacy tile")
        (legacy / "20260427_nodata_01_30.png").write_bytes(b"second legacy tile")
        (canonical / "20260427_nodata_01_29.png").write_bytes(b"partial canonical tile")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(ValueError, "Both RGB_png and tiles_png_1024"):
            find_dataset(self.root, "1150427")
        with self.assertRaisesRegex(ValueError, "Both RGB_png and tiles_png_1024"):
            prepare_downloads(self.root)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertTrue(legacy.is_dir())
        self.assertTrue(canonical.is_dir())

    def test_empty_alternate_tile_directory_is_safe(self):
        legacy = self.root / "1150427/RGB_png"
        canonical = self.root / "1150427/tiles_png_1024"
        legacy.mkdir(parents=True)
        canonical.mkdir()
        name = "20260427_nodata_01_29.png"
        (legacy / name).write_bytes(b"legacy")
        self.assertEqual(find_dataset(self.root, "1150427").tiles, legacy)
        prepare_downloads(self.root)
        self.assertEqual((canonical / name).read_bytes(), b"legacy")
        legacy.mkdir()
        self.assertEqual(find_dataset(self.root, "1150427").tiles, canonical)
        prepare_downloads(self.root)
        self.assertEqual((canonical / name).read_bytes(), b"legacy")

    def test_original_zip_layouts_import_to_one_structure_and_rerun_is_fast(self):
        members = [("image/RGB_png/20260427_nodata_01_29.png", b"April"),
                   ("1150618/tiles_png_1024/1150618_tile_0_8.png", b"June"),
                   ("1150806/tiles_png_1024/1150806_tile_0_7.png", b"August"),
                   ("1150618/1150618_input_orthomosaic-001.tif", b"TIFF"),
                   ("1150806/1150806_merged_with_boundaries_and_labels.png", b"overview"),
                   ("mask/20260427_nodata_01_29.png", b"not a source image"),
                   ("annotation.json", b"do not change")]
        archive = download(self.root, members)
        before = archive.read_bytes()
        prepare_downloads(self.root)
        expected = ["1150427/tiles_png_1024/20260427_nodata_01_29.png",
                    "1150618/tiles_png_1024/1150618_tile_0_8.png",
                    "1150806/tiles_png_1024/1150806_tile_0_7.png"]
        for path, contents in zip(expected, (b"April", b"June", b"August")):
            self.assertEqual((self.root / path).read_bytes(), contents)
        self.assertEqual(find_dataset(self.root, "1150618").image.read_bytes(), b"TIFF")
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse((self.root / "mask").exists())
        self.assertFalse((self.root / "annotation.json").exists())
        with patch("aerial_atlas.datasets.zipfile.ZipFile", side_effect=AssertionError("ZIP reopened")):
            prepare_downloads(self.root)

    def test_changed_target_or_conflicting_download_never_overwrites_an_image(self):
        member = "1150618/tiles_png_1024/1150618_tile_0_8.png"
        download(self.root, [(member, b"source")])
        prepare_downloads(self.root)
        target = self.root / member
        target.write_bytes(b"user edit")
        with self.assertRaises(FileExistsError):
            prepare_downloads(self.root)
        self.assertEqual(target.read_bytes(), b"user edit")
        self.assertFalse(list(target.parent.glob(".import-*.tmp")))

    def test_unsafe_paths_symlinks_and_case_collisions_are_rejected_before_publication(self):
        bad_members = ["../escape.png", "/escape.png", "C:/escape.png", "1150618\\..\\escape.png"]
        for index, name in enumerate(bad_members):
            with self.subTest(name=name):
                root = self.root / str(index)
                root.mkdir()
                download(root, [(name, b"bad"), ("1150618_tile_0_8.png", b"valid")])
                with self.assertRaisesRegex(ValueError, "Unsafe archive"):
                    prepare_downloads(root)
                self.assertFalse((root / "1150618").exists())
        root = self.root / "symlink"
        root.mkdir()
        link = zipfile.ZipInfo("1150618/tiles_png_1024/1150618_tile_0_8.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        download(root, [(link, b"outside")])
        with self.assertRaisesRegex(ValueError, "Unsafe archive"):
            prepare_downloads(root)
        root = self.root / "case"
        root.mkdir()
        download(root, [("RGB_png/20260427_nodata_01_29.png", b"one"),
                        ("RGB_png/20260427_NODATA_01_29.png", b"one")])
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            prepare_downloads(root)
        self.assertFalse((root / "1150427").exists())

    def test_existing_case_variant_is_not_duplicated(self):
        target = self.root / "1150427/tiles_png_1024/20260427_NODATA_01_29.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"one")
        download(self.root, [("RGB_png/20260427_nodata_01_29.png", b"one")])
        with self.assertRaisesRegex(ValueError, "Case-colliding"):
            prepare_downloads(self.root)
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_corrupt_member_is_not_published_and_a_repaired_download_retries(self):
        member = "1150618/tiles_png_1024/1150618_tile_0_8.png"
        archive = download(self.root, [(member, b"original payload")])
        correct = archive.read_bytes()
        archive.write_bytes(correct.replace(b"original payload", b"damaged! payload", 1))
        with self.assertRaises(zipfile.BadZipFile):
            prepare_downloads(self.root)
        self.assertFalse((self.root / member).exists())
        self.assertFalse(list(self.root.rglob(".import-*.tmp")))
        archive.write_bytes(correct)
        prepare_downloads(self.root)
        self.assertEqual((self.root / member).read_bytes(), b"original payload")

    def test_interrupted_archive_resumes_without_changing_completed_files(self):
        members = [("1150806_tile_0_7.png", b"first"), ("1150806_tile_0_8.png", b"second")]
        download(self.root, members)
        def cancel(stage, done, total, message):
            if done == 1:
                raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            prepare_downloads(self.root, cancel)
        first = self.root / "1150806/tiles_png_1024/1150806_tile_0_7.png"
        before = first.stat()
        self.assertFalse((self.root / ".imports.json").exists())
        prepare_downloads(self.root)
        self.assertEqual(first.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual((first.parent / "1150806_tile_0_8.png").read_bytes(), b"second")

    def test_data_root_fallback_override_and_loose_tiff_resolution(self):
        project = self.root / "app"
        (project / "images").mkdir(parents=True)
        sibling = self.root / "images"
        (sibling / "1150618/tiles_png_1024").mkdir(parents=True)
        loose = sibling / "1150618_input_orthomosaic-001.tif"
        loose.write_bytes(b"source TIFF")
        self.assertEqual(resolve_data_root(project), sibling.resolve())
        self.assertEqual(resolve_data_root(project, project / "images"), (project / "images").resolve())
        dataset = find_dataset(sibling, "1150618")
        self.assertEqual(dataset.image, loose.resolve())
        self.assertEqual(dataset.index_base, 0)
        download(sibling, [("1150618/" + loose.name, b"source TIFF")])
        prepare_downloads(sibling)
        self.assertFalse((sibling / "1150618" / loose.name).exists())
        self.assertEqual(find_dataset(sibling, "1150618").image, loose.resolve())
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            find_dataset(sibling, "../other")
        second = sibling / "1150618_input_orthomosaic-002.tif"
        second.write_bytes(b"another source")
        with self.assertRaisesRegex(ValueError, "Multiple source"):
            find_dataset(sibling, "1150618")
        (project / "images/1150427/RGB_png").mkdir(parents=True)
        self.assertEqual(resolve_data_root(project), (project / "images").resolve())
        self.assertEqual(DATASET_IDS, ("1150427", "1150618", "1150806"))

    def test_generic_zip_in_project_images_takes_priority_over_sibling_data(self):
        project = self.root / "app"
        own = project / "images"
        own.mkdir(parents=True)
        (self.root / "images/1150427/RGB_png").mkdir(parents=True)
        download(own, [("1150618_tile_0_8.png", b"new survey")], name="download.zip")
        self.assertEqual(resolve_data_root(project), own.resolve())
        prepare_downloads(resolve_data_root(project))
        self.assertEqual((own / "1150618/tiles_png_1024/1150618_tile_0_8.png").read_bytes(), b"new survey")
