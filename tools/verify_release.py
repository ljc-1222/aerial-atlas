"""Check the extracted Windows ZIP, its runtime, and synthetic reconstruction."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile

from PIL import Image
from aerial_atlas import __version__
from aerial_atlas.mosaic import sha256
from aerial_atlas.paths import source_dir

ROOT = Path(__file__).resolve().parents[1]


def verify():
    release = ROOT / "dist/releases"
    archive = release / "aerial-atlas-windows-x64.zip"
    digest, name = (release / "SHA256SUMS.txt").read_text().strip().split("  ", 1)
    if name != archive.name or sha256(archive) != digest:
        raise ValueError("Release checksum does not match")

    with tempfile.TemporaryDirectory(prefix="atlas release ") as directory:
        work = Path(directory).resolve()
        with ZipFile(archive) as stream:
            if stream.testzip() is not None:
                raise ValueError("ZIP CRC check failed")
            for entry in stream.infolist():
                if not (work / entry.filename).resolve().is_relative_to(work):
                    raise ValueError("Unsafe archive path")
            stream.extractall(work)
        app = work / "aerial-atlas"
        manifest = json.loads((app / "package_manifest.json").read_text(encoding="utf-8"))
        if manifest["version"] != __version__:
            raise ValueError("Release version does not match the project")
        expected = {item["path"] for item in manifest["files"]} | {"package_manifest.json"}
        actual = {path.relative_to(app).as_posix() for path in app.rglob("*") if path.is_file()}
        if actual != expected or any(path.startswith("docs/") for path in actual):
            raise ValueError("Release contains unexpected or missing files")
        for item in manifest["files"]:
            path = app / item["path"]
            if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise ValueError(f"Modified release file: {item['path']}")
        readme = (app / "README.txt").read_text(encoding="utf-8")
        if "AerialAtlas.exe" not in readme or "pip install" in readme:
            raise ValueError("Portable instructions do not describe the Windows application")

        env = os.environ.copy()
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        env["PYTHONNOUSERSITE"] = "1"
        for key in ("PYTHONHOME", "PYTHONPATH", "GDAL_DATA", "GDAL_DRIVER_PATH", "PROJ_LIB", "PROJ_DATA"):
            env.pop(key, None)
        exe = app / "AerialAtlas.exe"
        project = work / "synthetic project"
        command = [str(exe), "--project-dir", str(project), "--dataset", "1150427",
                   "--settings-path", str(work / "settings.json")]
        subprocess.run([*command, "--demo", "--build"],
                       cwd=work, env=env, check=True, timeout=120)
        result = work / "runtime-check.json"
        subprocess.run([*command, "--tile", "2_2",
                        "--self-test-report", str(result)], cwd=work, env=env, check=True, timeout=60)
        report = json.loads(result.read_text())
        with Image.open(source_dir(project) / "demo_02_02.png") as image:
            expected_rgb = hashlib.sha256(image.tobytes()).hexdigest()
        if not (report["status"] == "passed" and report["frozen"] and report["tkinter"]
                and report["tiles"] == 14 and report["reference_rgb_sha256"] == expected_rgb):
            raise ValueError("Native reconstruction or rendering failed")
        print("Windows release verified: ZIP, file hashes, native reconstruction, Tk and RGB rendering")


if __name__ == "__main__":
    verify()
