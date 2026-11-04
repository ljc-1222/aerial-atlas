"""One small public fixture per test process, constructed without private inputs."""
import atexit
from pathlib import Path
import tempfile
from aerial_atlas.demo import create_demo
from aerial_atlas.mosaic import build, current_map

_fixture = None


def survey_downloads(project: Path, id="1150618"):
    """Small stand-ins for a downloaded tile ZIP and its separate source TIFF."""
    import io
    import zipfile
    import numpy as np
    from PIL import Image
    import rasterio
    from rasterio.transform import from_origin

    downloads = project / "images"
    downloads.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(downloads / f"{id}-download.zip", "w") as archive:
        for column, color in ((0, "red"), (1, "blue")):
            buffer = io.BytesIO()
            Image.new("RGB", (1024, 1024), color).save(buffer, format="PNG")
            archive.writestr(f"{id}/tiles_png_1024/{id}_tile_0_{column}.png", buffer.getvalue())
    image = downloads / f"{id}_input_orthomosaic-001.tif"
    with rasterio.open(image, "w", driver="GTiff", width=1536, height=1280,
                       count=8, dtype="uint16", crs="EPSG:3826",
                       transform=from_origin(305000, 2785000, .03, .03),
                       tiled=True, blockxsize=256, blockysize=256, compress="deflate") as dataset:
        pixels = np.full((8, 1280, 1536), 32768, dtype="uint16")
        pixels[7] = 65535
        pixels[7, :256] = 0
        dataset.write(pixels)
    return image


def fixture():
    global _fixture
    if _fixture is None:
        folder = tempfile.TemporaryDirectory(prefix="atlas synthetic ")
        atexit.register(folder.cleanup)
        project = Path(folder.name)
        source = project / "images/1150427/RGB_png"
        output = project / "generated/1150427"
        create_demo(source)
        report = build(source, output)
        _fixture = dict(source=source, output=output, report=report, **current_map(output))
    return _fixture
