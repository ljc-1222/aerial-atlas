"""Collect complete notices for components actually present in a frozen bundle."""
from importlib import metadata
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sys

from PyInstaller.archive.readers import CArchiveReader


def _normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _module_name(file):
    path = PurePosixPath(str(file))
    if ".." in path.parts or path.suffix not in (".py", ".pyc", ".pyd", ".so"):
        return None
    parts = list(path.parts[:-1]) + [path.name.split(".", 1)[0]]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _selected_distributions(modules, bundled_files):
    # Python 3.10's packages_distributions() only reads top_level.txt; wheel
    # RECORDs also cover modern wheels without it, native extensions and data.
    roots = {module.split(".", 1)[0] for module in modules}
    declared = {
        _normalized(name)
        for package, names in metadata.packages_distributions().items()
        if package in roots
        for name in names
    }
    selected = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        key = _normalized(name)
        if key == "aerial-atlas":
            continue  # The application's own LICENSE is distributed separately.
        files = tuple(distribution.files or ())
        if any(
            _module_name(file) in modules or file.as_posix().casefold() in bundled_files
            for file in files
        ) or (not files and key in declared):
            selected[key] = distribution
    return [selected[key] for key in sorted(selected)]


def _notice_sources(distribution, modules, bundled_files):
    vendor_cache = {}
    for file in distribution.files or ():
        parts = file.parts
        if ".." in parts or file.suffix in (".py", ".pyc"):
            continue
        if not any(word in file.name.lower() for word in ("license", "licence", "copying", "copyright", "notice", "authors")):
            continue
        source = Path(distribution.locate_file(file))
        if not source.is_file():
            continue
        if "_vendor" in parts or "vendored" in parts:
            nested = next((i for i, part in enumerate(parts) if part.endswith(".dist-info")), None)
            if nested is not None:
                package = PurePosixPath(*parts[:nested + 1])
                if package not in vendor_cache:
                    vendor = metadata.PathDistribution(Path(distribution.locate_file(package)))
                    prefix = ".".join(parts[:nested])
                    vendor_cache[package] = (
                        any(f"{prefix}.{_module_name(owned)}" in modules for owned in vendor.files or ())
                        or any(path.startswith(package.as_posix().casefold() + "/") for path in bundled_files)
                    )
                if not vendor_cache[package]:
                    continue
            else:
                # Some notices live inside a vendored package, such as wheel's
                # vendored packaging. Match that package, not every vendor.
                prefix = ".".join(parts[:-1])
                if not any(module == prefix or module.startswith(prefix + ".") for module in modules):
                    continue
        # Native-extension notices may cover compiled source/header folders
        # that are not themselves copied into the frozen application.
        yield file.as_posix(), source


def _short_license(distribution):
    expression = distribution.metadata.get("License-Expression", "")
    if expression and len(expression) <= 120 and "\n" not in expression:
        return expression
    legacy = distribution.metadata.get("License", "")
    return legacy if legacy in {
        "MIT", "MIT-CMU", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
        "MPL-2.0", "PSF-2.0", "ISC", "Zlib", "Unlicense",
    } else "See notices"


def _copy_notices(destination, component, sources):
    """Keep each distinct complete text once per component, with flat paths."""
    copied, seen = [], set()
    for _, source in sorted(sources, key=lambda item: item[0]):
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        target = destination / component / source.name
        if target.exists():
            target = target.with_name(f"{target.stem}-{digest[:12]}{target.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        copied.append(target.relative_to(destination).as_posix())
    if not copied:
        raise RuntimeError(f"No complete license notices found for bundled component: {component}")
    return copied


def collect(destination: Path, bundle: Path):
    destination, bundle = Path(destination), Path(bundle)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("License output must be empty to avoid retaining stale notices")
    internal = bundle / "_internal"
    if not internal.is_dir():
        raise FileNotFoundError("The frozen bundle's _internal folder is missing")
    modules = set(CArchiveReader(str(bundle / "AerialAtlas.exe")).open_embedded_archive("PYZ.pyz").toc)
    bundled_files = {path.relative_to(internal).as_posix().casefold() for path in internal.rglob("*") if path.is_file()}
    components = []
    for distribution in _selected_distributions(modules, bundled_files):
        name = distribution.metadata["Name"]
        files = _copy_notices(destination, _normalized(name), _notice_sources(distribution, modules, bundled_files))
        components.append(dict(name=name, version=distribution.version, license=_short_license(distribution), files=files))

    base = Path(sys.base_prefix)
    tcl = base / "tcl/tcl8.6/license.terms"
    if not tcl.is_file():
        tcl = Path(__file__).resolve().parent / "resources/tcl-license.txt"
    for name, version, license_id, source in (
        ("Python", platform.python_version(), "PSF-2.0", base / "LICENSE.txt"),
        ("Tcl", "8.6", "See notices", tcl),
        ("Tk", "8.6", "See notices", base / "tcl/tk8.6/license.terms"),
    ):
        if not source.is_file():
            raise FileNotFoundError(f"Required runtime notice not found: {source.name}")
        if name in ("Tcl", "Tk"):
            script = internal / f"_{name.lower()}_data" / ("init.tcl" if name == "Tcl" else "tk.tcl")
            if script.is_file():
                match = re.search(rf"package require -exact {name}\s+([0-9.]+)", script.read_text(encoding="utf-8"))
                if match:
                    version = match[1]
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{name}.txt"
        shutil.copy2(source, target)
        components.append(dict(name=name, version=version, license=license_id, files=[target.name]))
    inventory = dict(components=components)
    (destination / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args()
    collect(options.destination, options.bundle)
