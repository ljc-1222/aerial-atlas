"""Small, deterministic, original synthetic imagery for public examples and tests."""
from pathlib import Path
import numpy as np
from PIL import Image


def create_demo(source: Path):
    if source.exists() and any(source.iterdir()):
        raise FileExistsError("Demo creation needs an empty source folder. Choose a separate --project-dir.")
    source.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[:2200, :2350]
    pixels = np.empty((2200, 2350, 3), dtype=np.uint8)
    # Survey-like blocks, a blue corridor, and valid black pixels. No real imagery.
    pixels[..., 0] = 45 + ((x // 120 + y // 170) % 5) * 20
    pixels[..., 1] = 100 + ((x // 180 + y // 130) % 5) * 18
    pixels[..., 2] = 65 + ((x + y) // 90 % 5) * 12
    pixels[np.abs(x - y*.65 - 360) < 75] = (76, 128, 174)
    pixels[(x % 512 < 10) | (y % 512 < 10)] = (226, 218, 171)
    pixels[700:780, 700:780] = 0
    for row in range(1, 5):
        for col in range(1, 5):
            if (row, col) in ((1, 1), (3, 2)):
                continue
            xx, yy = (col-1)*512, (row-1)*512
            Image.fromarray(pixels[yy:min(yy+1024, 2200), xx:min(xx+1024, 2350)]).save(
                source / f"demo_{row:02}_{col:02}.png")
