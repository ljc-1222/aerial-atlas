from pathlib import Path
import unittest
import numpy as np
from PIL import Image
from aerial_atlas.mosaic_viewer import (Camera, ImageCache, RenderRequest, RenderResult, WorldFrame, compose_view)

class CacheTests(unittest.TestCase):
    def test_lru_eviction_tracks_bytes_and_recent_access(self):
        cache = ImageCache(80_000)
        a, b, c = (Image.new("RGBA", (100, 100), color) for color in ("red", "green", "blue"))
        cache.put((1,), a)
        cache.put((2,), b)
        self.assertIs(cache.get((1,)), a)
        cache.put((3,), c)
        self.assertIsNone(cache.get((2,)))
        self.assertIs(cache.get((1,)), a)
        self.assertEqual(cache.bytes_used, 80_000)
        cache.put((4,), Image.new("RGBA", (200, 200)))
        self.assertLessEqual(cache.bytes_used, cache.limit_bytes)
        cache.clear()
        self.assertEqual((cache.bytes_used, len(cache.entries)), (0, 0))

    def test_zero_budget_disables_cache(self):
        cache = ImageCache(0)
        cache.put((1,), Image.new("RGBA", (5, 5)))
        self.assertEqual(cache.bytes_used, 0)
        self.assertIsNone(cache.get((1,)))


class ProgressiveDisplayTests(unittest.TestCase):
    def setUp(self):
        self.overview = WorldFrame(Image.new("RGBA", (64, 64), (40, 90, 70, 255)), 0, 0, 2048, 2048)
        self.detail = WorldFrame(Image.new("RGBA", (512, 512), (200, 60, 30, 255)), 512, 512, 512, 512)

    def test_pan_reprojects_detail_and_fills_newly_exposed_area(self):
        camera = Camera(1060, 768, 1)
        image = compose_view(self.overview, self.detail, camera, 200, 200, interactive=True)
        self.assertEqual(image.getpixel((20, 100)), (200, 60, 30, 255))
        self.assertEqual(image.getpixel((150, 100)), (40, 90, 70, 255))

    def test_rgb_display_preserves_alpha_background_and_valid_black(self):
        pixels = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        pixels.paste((0, 0, 0, 255), (10, 10, 50, 90))
        pixels.paste((200, 60, 30, 128), (50, 10, 90, 90))
        frame = WorldFrame(pixels, 0, 0, 100, 100)
        camera = Camera(50, 50, 1)
        for color in ((232, 238, 234, 255), (24, 35, 31, 255)):
            rgba = compose_view(frame, frame, camera, 100, 100, background=color)
            rgb = compose_view(frame, frame, camera, 100, 100, background=color, opaque=True)
            self.assertEqual(rgb.mode, "RGB")
            self.assertEqual(rgb.getpixel((20, 20)), (0, 0, 0))
            self.assertLessEqual(np.abs(np.asarray(rgb).astype(int) - np.asarray(rgba)[:, :, :3]).max(), 1)

    def test_zoom_retains_centered_detail_and_overview_without_fresh_read(self):
        camera = Camera(768, 768, 0.25)
        image = compose_view(self.overview, self.detail, camera, 200, 200, interactive=True)
        self.assertEqual(image.getpixel((100, 100)), (200, 60, 30, 255))
        self.assertEqual(image.getpixel((0, 100)), (40, 90, 70, 255))
        self.assertEqual((camera.cx, camera.cy, camera.scale), (768, 768, 0.25))

    def test_large_jump_uses_correct_overview_not_old_detail(self):
        image = compose_view(self.overview, self.detail, Camera(1600, 1600, 1), 200, 200, interactive=True)
        self.assertEqual(image.getpixel((100, 100)), (40, 90, 70, 255))

    def test_theme_only_changes_transparent_gaps_and_preserves_valid_black(self):
        pixels = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        pixels.paste((0, 0, 0, 255), (25, 25, 75, 75))
        frame = WorldFrame(pixels, 0, 0, 100, 100)
        for background in ((232, 238, 234, 255), (24, 35, 31, 255)):
            result = compose_view(frame, frame, Camera(50, 50, 1), 100, 100, background=background)
            self.assertEqual(result.getpixel((0, 0)), background)
            self.assertEqual(result.getpixel((50, 50)), (0, 0, 0, 255))

    def test_capped_texture_keeps_correct_world_geometry(self):
        result = RenderResult(RenderRequest(4, 768, 768, 1, 512, 512), Image.new("RGBA", (64, 64), (200, 60, 30, 255)))
        frame = WorldFrame.from_result(result)
        image = compose_view(self.overview, frame, Camera(1060, 768, 1), 200, 200, interactive=True)
        self.assertEqual(image.getpixel((20, 100)), (200, 60, 30, 255))
        self.assertEqual(image.getpixel((150, 100)), (40, 90, 70, 255))

