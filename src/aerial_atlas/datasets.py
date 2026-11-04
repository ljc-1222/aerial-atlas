"""Recognize survey downloads and import their original imagery without renaming files."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile
import zlib

from .paths import atomic_json

DATASET_IDS = ("1150427", "1150618", "1150806")
CHUNK = 1024 * 1024


def survey_date(id: str) -> str:
    return f"{int(id[:3]) + 1911}-{id[3:5]}-{id[5:]}"


def survey_states(project: Path, data_root: Path) -> dict[str, str]:
    states = {}
    for id in DATASET_IDS:
        try:
            dataset = find_dataset(data_root, id)
            if (project / "generated" / id / "current.json").is_file():
                states[id] = "prepared"
            elif (dataset.tiles.is_dir() and any(p.suffix.lower() == ".png" for p in dataset.tiles.iterdir())
                  and (dataset.index_base == 1 or dataset.image is not None)):
                states[id] = "available"
            else:
                states[id] = "missing"
        except (OSError, ValueError):
            states[id] = "invalid"
    return states


@dataclass(frozen=True)
class Dataset:
    id: str
    tiles: Path
    image: Path | None
    index_base: int


def _tiles_dir(folder: Path) -> Path:
    canonical, legacy = folder / "tiles_png_1024", folder / "RGB_png"
    canonical_ready = canonical.is_dir() and any(canonical.iterdir())
    legacy_ready = legacy.is_dir() and any(legacy.iterdir())
    if canonical_ready and legacy_ready:
        raise ValueError(f"Both RGB_png and tiles_png_1024 contain files for {folder.name}; keep one complete tile folder.")
    return legacy if legacy_ready or (not canonical.is_dir() and legacy.is_dir()) else canonical


def find_dataset(data_root: Path, id: str) -> Dataset:
    if id not in DATASET_IDS:
        raise ValueError(f"Unknown dataset: {id}")
    folder = data_root / id
    tiles = _tiles_dir(folder)
    images = {path.resolve() for parent in (folder, data_root) if parent.is_dir()
              for path in parent.iterdir() if path.is_file()
              and re.fullmatch(rf"{id}_input_orthomosaic(?:-[\w.-]+)?\.tiff?", path.name, re.I)}
    if len(images) > 1:
        raise ValueError(f"Multiple source orthomosaics found for {id}; keep one source TIFF.")
    return Dataset(id, tiles, next(iter(images), None), 1 if id == "1150427" else 0)


def _has_inputs(root: Path) -> bool:
    return any((root / id / name).is_dir() for id in DATASET_IDS
               for name in ("tiles_png_1024", "RGB_png")) or any(
        path.is_file() and (path.suffix.lower() == ".zip" or (
            path.suffix.lower() in (".tif", ".tiff") and path.name.startswith(DATASET_IDS)
        ))
        for parent in (root, *(root / id for id in DATASET_IDS)) if parent.is_dir()
        for path in parent.iterdir())


def resolve_data_root(project: Path, override: Path | None = None) -> Path:
    if override is not None:
        return override.resolve()
    own, sibling = project / "images", project.parent / "images"
    return (sibling if not _has_inputs(own) and _has_inputs(sibling) else own).resolve()


def _member_target(member: zipfile.ZipInfo) -> Path | None:
    name = member.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in name or stat.S_ISLNK(member.external_attr >> 16):
        raise ValueError(f"Unsafe archive path: {member.filename}")
    if member.is_dir() or not path.parts:
        return None
    filename, parents = path.name, path.parts[:-1]
    april = re.fullmatch(r"20260427_nodata_\d+_\d+\.png", filename, re.I)
    newer = re.fullmatch(r"(1150618|1150806)_tile_\d+_\d+\.png", filename, re.I)
    if april or newer:
        id = "1150427" if april else newer[1]
        folders = ("tiles_png_1024", "RGB_png") if april else ("tiles_png_1024",)
        accepted = {(), ("image",)} if april else {()}
        for folder in folders:
            accepted.update({(folder,), (id, folder), ("images", id, folder), ("image", folder)})
        if parents in accepted:
            return Path(id, "tiles_png_1024", filename)
    for id in DATASET_IDS:
        if parents not in ((), (id,), ("images", id)):
            continue
        if re.fullmatch(rf"{id}_input_orthomosaic(?:-[\w.-]+)?\.tiff?", filename, re.I) or (
            filename == f"{id}_merged_with_boundaries_and_labels.png"
        ):
            return Path(id, filename)
    return None


def _confined(root: Path, path: Path) -> Path:
    target = path.resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError(f"Import destination is outside the data folder: {path}")
    return target


def _file_crc(path: Path) -> int:
    crc = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            crc = zlib.crc32(chunk, crc)
    return crc


def _unchanged(record: dict, archive: Path, root: Path) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("members"), list) or not record["members"]:
        return False
    archive_stat = archive.stat()
    if (record.get("bytes"), record.get("mtime_ns")) != (archive_stat.st_size, archive_stat.st_mtime_ns):
        return False
    try:
        for member in record["members"]:
            target = _confined(root, root / member["path"])
            info = target.stat()
            if not target.is_file() or (info.st_size, info.st_mtime_ns) != (member["bytes"], member["mtime_ns"]):
                return False
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return True


def _import_file(archive, member, target, progress, done, total):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or target.stat().st_size != member.file_size or _file_crc(target) != member.CRC:
            raise FileExistsError(f"Downloaded imagery conflicts with existing file: {target.name}")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".import-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            with archive.open(member) as source:
                for chunk in iter(lambda: source.read(CHUNK), b""):
                    output.write(chunk)
                    progress("importing", done, total, Path(archive.filename).name)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != member.file_size:
            raise ValueError(f"Incomplete archive member: {member.filename}")
        # Windows rename fails if a competing importer created the destination.
        if os.name == "nt":
            temporary.rename(target)
        else:
            os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_downloads(data_root: Path, progress=lambda *args: None) -> None:
    """Import recognized ZIP members; originals survive cancellation and retries."""
    from .mosaic import build_lock

    root = data_root.resolve()
    for id in DATASET_IDS:
        _tiles_dir(root / id)
    with build_lock(root):
        record_path = root / ".imports.json"
        try:
            records = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(records, dict):
                records = {}
        except (OSError, ValueError):
            records = {}
        legacy = root / "1150427" / "RGB_png"
        canonical = root / "1150427" / "tiles_png_1024"
        if legacy.is_dir() and (not canonical.exists() or (canonical.is_dir() and not any(canonical.iterdir()))):
            _confined(root, legacy)
            _confined(root, canonical)
            if canonical.is_dir():
                canonical.rmdir()
            legacy.rename(canonical)
        archives = sorted(path for folder in (root, *(root / id for id in DATASET_IDS)) if folder.is_dir()
                          for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".zip")
        pending = [path for path in archives
                   if not _unchanged(records.get(path.relative_to(root).as_posix(), {}), path, root)]
        destinations = {}
        existing_names = {}
        plans = []
        for path in pending:
            members = []
            archive_destinations = set()
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    relative = _member_target(member)
                    if relative is None:
                        continue
                    key = relative.as_posix().casefold()
                    previous = destinations.get(key)
                    if key in archive_destinations or (previous is not None and (
                        previous[0] != relative or previous[1:] != (member.file_size, member.CRC)
                    )):
                        raise ValueError(f"Conflicting or case-colliding archive members: {relative}")
                    archive_destinations.add(key)
                    destinations[key] = (relative, member.file_size, member.CRC)
                    loose = root / relative.name
                    destination = loose if relative.suffix.lower() in (".tif", ".tiff") and loose.is_file() else root / relative
                    target = _confined(root, destination)
                    if target.parent not in existing_names:
                        existing_names[target.parent] = {p.name.casefold(): p.name for p in target.parent.iterdir()} if target.parent.is_dir() else {}
                    names = existing_names[target.parent]
                    if names.get(relative.name.casefold(), relative.name) != relative.name:
                        raise ValueError(f"Case-colliding archive member: {relative}")
                    members.append((member, target))
            if members:
                plans.append((path, members))
        total = sum(len(members) for _, members in plans)
        done = 0
        for path, members in plans:
            before = path.stat()
            completed = []
            with zipfile.ZipFile(path) as archive:
                for member, target in members:
                    progress("importing", done, total, path.name)
                    _import_file(archive, member, target, progress, done, total)
                    done += 1
                    info = target.stat()
                    completed.append(dict(path=target.relative_to(root).as_posix(), bytes=info.st_size, mtime_ns=info.st_mtime_ns))
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Download changed while importing: {path.name}")
            records[path.relative_to(root).as_posix()] = dict(bytes=after.st_size, mtime_ns=after.st_mtime_ns, members=completed)
            atomic_json(record_path, records)
        progress("importing", done, total, "")
