# Aerial Atlas

Build a local mosaic from numbered orthophoto tiles and inspect each tile in context.

**[Download for Windows (64-bit)](https://github.com/ljc-1222/aerial-atlas/releases/latest/download/aerial-atlas-windows-x64.zip)** · [All releases](https://github.com/ljc-1222/aerial-atlas/releases)

Extract the entire ZIP and run **`AerialAtlas.exe`**. Keep the `_internal` folder
beside it. Python is not required.

![Aerial Atlas displaying synthetic example tiles](docs/assets/demo.png)

## First use

The dataset is provided separately by its owner and is not included in this project.
Put the downloaded `1150427` folder in `<app folder>/images/`:

```text
aerial-atlas/
  AerialAtlas.exe
  _internal/
  images/
    1150427/
      RGB_png/
        survey_01_02.png
        ...
```

Open the application and choose **Create map**. Creation may take several minutes;
leave the source files unchanged until it finishes. You can cancel and retry.
Once complete, the viewer opens automatically, and later launches reuse the map
saved in `generated/1150427/`.

## Explore

- Select a tile ID or search a filename / `row_column` to outline and center it
  with **1.5× context**.
- Filter by row or use Previous / Next to browse. Right-click the map to choose
  between overlapping tiles.
- Drag to pan, use the mouse wheel to zoom, and press Home to fit the map.
- Open Settings to change language, appearance or display options. The Help menu
  lists keyboard shortcuts.

Coordinates are image pixels, not GPS coordinates. Reconstruction requires RGB
PNGs on a 512-pixel grid with identical overlaps; see the
[dataset requirements](docs/dataset.md).

## Try without a dataset

Run this command from the extracted application folder in PowerShell:

```powershell
.\AerialAtlas.exe --demo --project-dir "$env:TEMP\aerial-atlas-demo"
```

Choose **Create map** when the window opens. The demo creates synthetic tiles and
requires an empty destination source folder.

## Troubleshooting

| Problem | What to check |
|---|---|
| Tiles not found | The exact path is `images/1150427/RGB_png/`, beside the application. |
| Overlapping pixels disagree | Use unchanged tiles from the same dataset and check their numbering. |
| Not enough space or permission denied | Use a writable folder and free the space indicated by the application. |
| App will not start or a DLL is missing | Extract the whole ZIP again; copying only the EXE is insufficient. |

[Report an issue](https://github.com/ljc-1222/aerial-atlas/issues) with the app
version, error message and whether the demo works. Do not attach private imagery
or unredacted logs.

## Development and license

See [CONTRIBUTING.md](CONTRIBUTING.md) to run from source, test and build the app.

Licensed under [Apache-2.0](LICENSE). Bundled dependencies retain their own
licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
