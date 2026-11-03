"""Relocatable project paths and small atomic JSON records."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time

DATASET = "1150427"
SCHEMA = 1


def default_project() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return Path.cwd().resolve()


def source_dir(project: Path) -> Path:
    return project / "images" / DATASET / "RGB_png"


def output_dir(project: Path) -> Path:
    return project / "generated" / DATASET


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".record-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/antivirus can briefly deny delete-sharing. Keep atomic
        # replacement, but tolerate a short sharing violation instead of failing a build.
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.02 * (attempt+1))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
