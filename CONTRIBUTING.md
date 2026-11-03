# Development

Use Windows x64 and Python 3.10 x64 with Tk. Run these commands from the project
root:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/build-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m aerial_atlas
```

For a source checkout, place the dataset in `images/1150427/RGB_png/` under the
project root. Dataset files and generated maps must stay out of Git.

## Tests and changes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_repo.py
```

Tests use synthetic images and do not require the private dataset. Add regression
coverage for behavior changes, especially pixel-exact reconstruction, tile
location and background rendering. Keep changes focused and describe their
effect and validation in the pull request.

Application code lives in `src/aerial_atlas/`: `mosaic` and `reconstruction` build
the map, `startup` handles first use, and `mosaic_viewer` with `viewer_*` provides
the viewer. Preserve bounded background work and publish a generated map only
after verification succeeds.

## Build a Windows package

Use a fresh checkout for release builds. Run the GUI smoke check on an interactive
Windows desktop before packaging:

```powershell
.\.venv\Scripts\python.exe tools/validate_gui.py
.\.venv\Scripts\python.exe tools/build_portable.py
.\.venv\Scripts\python.exe tools/package_release.py
.\.venv\Scripts\python.exe tools/verify_release.py
```

The application folder is written to `dist/windows/aerial-atlas/`; the Windows
ZIP and checksums are written to `dist/releases/`. Build tools preserve existing
outputs. Dependency changes require updating the pinned versions and rebuilding
the bundled license notices.

## Publish

Update `__version__` in `src/aerial_atlas/__init__.py`, commit the change, and push
a matching `v<version>` tag. The release workflow builds the Windows package and
attaches it to a GitHub Release. GitHub provides source archives automatically.
Manual workflow runs produce build artifacts without publishing a release.

Contributions are accepted under Apache-2.0. Retain applicable third-party
copyright and license notices.
