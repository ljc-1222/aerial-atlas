"""Bounded display reads for RGB maps and the supplied multispectral surveys."""
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling


def validate_raster(dataset) -> None:
    if (dataset.count, dataset.dtypes) not in (
        (3, ("uint8",) * 3), (8, ("uint16",) * 8),
    ):
        raise ValueError("Expected a 3-band uint8 RGB map or an 8-band uint16 survey TIFF.")


def inspect_raster(path: Path) -> dict:
    with rasterio.open(path) as dataset:
        validate_raster(dataset)
        return dict(width=dataset.width, height=dataset.height, count=dataset.count,
                    dtypes=list(dataset.dtypes), crs=str(dataset.crs) if dataset.crs else None,
                    transform=list(dataset.transform), internal_overviews=dataset.overviews(1))


def read_display(dataset, window, out_shape: tuple[int, int]) -> np.ndarray:
    validate_raster(dataset)
    rgb = dataset.read((1, 2, 3), window=window, out_shape=(3, *out_shape),
                       resampling=Resampling.bilinear)
    if dataset.count == 8:
        # One fixed scale keeps a pixel's colour unchanged when the viewport moves.
        rgb = ((rgb.astype(np.uint32) + 128) // 257).astype(np.uint8)
        alpha = dataset.read(8, window=window, out_shape=out_shape, resampling=Resampling.nearest)
        mask = (alpha > 0).astype(np.uint8) * 255
    else:
        mask = dataset.dataset_mask(window=window, out_shape=out_shape, resampling=Resampling.nearest)
    rgba = np.empty((*out_shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb.transpose(1, 2, 0)
    rgba[:, :, 3] = mask
    return rgba
