"""Release notices follow frozen contents rather than the build environment."""
from pathlib import Path, PurePosixPath
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from licenses import _copy_notices, _notice_sources, _selected_distributions, _short_license


class Distribution:
    def __init__(self, root, name, files, license_text="MIT"):
        self.root = root
        self.metadata = {"Name": name, "License": license_text}
        self.files = [PurePosixPath(file) for file in files]
        self.version = "1.0"

    def locate_file(self, file):
        return self.root / str(file)


class LicenseTests(unittest.TestCase):
    def test_modules_without_top_level_metadata_and_binary_data_select_their_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distributions = [
                Distribution(root, "runtime", ["runtime/__init__.py"]),
                Distribution(root, "native", ["native.libs/driver.dll"]),
                Distribution(root, "builder", ["builder/__init__.py"]),
                Distribution(root, "aerial-atlas", ["aerial_atlas/__init__.py"]),
            ]
            with patch("licenses.metadata.packages_distributions", return_value={}), patch("licenses.metadata.distributions", return_value=distributions):
                actual = _selected_distributions({"runtime", "aerial_atlas"}, {"native.libs/driver.dll"})
            self.assertEqual({item.metadata["Name"] for item in actual}, {"runtime", "native"})

    def test_declared_module_aliases_are_respected(self):
        distribution = Distribution(Path("unused"), "Pillow", [])
        with patch("licenses.metadata.packages_distributions", return_value={"PIL": ["Pillow"]}), patch("licenses.metadata.distributions", return_value=[distribution]):
            self.assertEqual(_selected_distributions({"PIL.Image"}, set()), [distribution])

    def test_shared_namespace_does_not_include_unbundled_distribution(self):
        used = Distribution(Path("unused"), "used", ["namespace/used.py"])
        unused = Distribution(Path("unused"), "unused", ["namespace/unused.py"])
        with patch("licenses.metadata.packages_distributions", return_value={"namespace": ["used", "unused"]}), patch("licenses.metadata.distributions", return_value=[used, unused]):
            self.assertEqual(_selected_distributions({"namespace", "namespace.used"}, set()), [used])

    def test_identical_notices_are_deduplicated_but_different_notices_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for number, content in enumerate((b"same complete notice", b"same complete notice", b"another complete notice")):
                path = root / str(number) / "LICENSE"
                path.parent.mkdir()
                path.write_bytes(content)
                sources.append((str(number), path))
            files = _copy_notices(root / "output", "runtime", sources)
            self.assertEqual(len(files), 2)
            self.assertEqual({(root / "output" / file).read_bytes() for file in files}, {b"same complete notice", b"another complete notice"})

    def test_unused_vendored_distribution_is_not_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ["runtime-1.dist-info/LICENSE", "runtime/_vendor/unused-1.dist-info/LICENSE"]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True)
                path.write_text(name)
            distribution = Distribution(root, "runtime", names)
            vendor = Distribution(root, "unused", ["unused/__init__.py"])
            with patch("licenses.metadata.PathDistribution", return_value=vendor):
                selected = list(_notice_sources(distribution, {"runtime"}, set()))
            self.assertEqual([name for name, _ in selected], [names[0]])
            with patch("licenses.metadata.PathDistribution", return_value=vendor):
                selected = list(_notice_sources(distribution, {"runtime", "runtime._vendor.unused"}, set()))
            self.assertEqual(len(selected), 2)

    def test_long_legacy_metadata_is_not_copied_into_inventory(self):
        distribution = Distribution(Path("unused"), "runtime", [], "Copyright\n" + "long text" * 1000)
        self.assertEqual(_short_license(distribution), "See notices")
        distribution.metadata["License-Expression"] = "Apache-2.0 OR BSD-2-Clause"
        self.assertEqual(_short_license(distribution), "Apache-2.0 OR BSD-2-Clause")
