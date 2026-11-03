from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import rasterio
from aerial_atlas.mosaic_viewer import (Camera, TileIndex, TileBrowser, RenderWorker, RenderRequest,
                                        render_region, CONTEXT_FACTOR)
from support import fixture
from aerial_atlas.viewer_preferences import Preferences, migrate_view_identity
from aerial_atlas.viewer_navigation import ViewState


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()
        cls.index = TileIndex(cls.data["index"])

    def test_numeric_order_lookup_and_windows_path(self):
        index = self.index
        self.assertEqual(len(index.tiles), 14)
        self.assertEqual([(t.row,t.column) for t in index.tiles], sorted(index.by_position))
        for tile in index.tiles:
            self.assertIs(index.find(tile.filename.upper()), tile)
            self.assertIs(index.find(Path(tile.filename).stem), tile)
            self.assertIs(index.find(tile.identifier), tile)
        self.assertIs(index.find('"C:\\test\\DEMO_01_02.PNG"'), index.find("1_2"))
        self.assertIsNone(index.find("3_2"))
        self.assertIsNone(index.find("wrong_01_02.png"))

    def test_one_point_five_context_centered_on_cropped_edges(self):
        self.assertEqual(CONTEXT_FACTOR, 1.5)
        for tile in self.index.tiles:
            camera = Camera(0, 0)
            camera.locate(tile, 1200, 800)
            self.assertEqual((camera.cx,camera.cy), tile.center)
            self.assertAlmostEqual(min(1200/camera.scale/tile.width, 800/camera.scale/tile.height), 1.5)
        self.assertEqual(self.index.find("4_4").width, 814)
        self.assertEqual(self.index.find("4_4").height, 664)

    def test_browse_boundaries_missing_tiles_filters_and_overlap_pick(self):
        browser = TileBrowser(self.index)
        results = browser.filter(row=3)
        self.assertEqual([t.column for t in results], [1,3,4])
        self.assertEqual(browser.neighbor(results[0],1).column, 3)
        self.assertIsNone(browser.neighbor(results[0],-1))
        self.assertIsNone(browser.neighbor(results[-1],1))
        self.assertIs(browser.neighbor(None,1),results[0])
        self.assertIs(browser.neighbor(None,-1),results[-1])
        snapshot = list(browser.tiles)
        browser.neighbor(results[0],1)
        self.assertEqual(browser.tiles,snapshot)
        self.assertEqual(browser.filter("missing"),[])
        self.assertIsNone(browser.neighbor(None,1))
        self.assertEqual([t.identifier for t in self.index.at_point(800,800)], ["01_02","02_01","02_02"])
        self.assertEqual(self.index.at_point(0,0),[])
        self.assertEqual(self.index.at_point(self.index.width,self.index.height),[])
        tile = self.index.find("4_4")
        self.assertNotIn(tile,self.index.at_point(tile.x+tile.width,tile.y))

    def test_one_to_one_render_and_wheel_anchor(self):
        tile = self.index.find("2_2")
        with rasterio.open(self.data["image"]) as dataset:
            image = render_region(dataset,RenderRequest(1,*tile.center,1,tile.width,tile.height),2_000_000)
        with Image.open(self.data["source"]/tile.filename) as source:
            np.testing.assert_array_equal(np.asarray(image)[...,:3], np.asarray(source))
        camera = Camera(*tile.center,.75)
        before = camera.world_at(137,293,1200,800)
        camera.zoom(1.2,137,293,1200,800,.01)
        np.testing.assert_allclose(camera.world_at(137,293,1200,800),before,atol=1e-10,rtol=0)

    def test_old_camera_migrates_only_with_identical_source_index(self):
        state=dict(map_key=str(self.data["image"]),extent=[2350,2200],state=ViewState(1000,900,.5).to_dict())
        prefs=Preferences(last_view=state)
        identity="aerial-atlas:v1:"+self.data["manifest"]["index_sha256"]
        migrated=migrate_view_identity(prefs,self.data["index"],identity,[2350,2200])
        self.assertEqual(migrated["state"],state["state"])
        self.assertEqual(migrated["map_key"],identity)
        self.assertNotEqual(prefs.last_view["map_key"],identity)
        self.assertIsNone(migrate_view_identity(prefs,self.data["index"],identity,[1,2]))

    def test_rapid_requests_are_coalesced_and_cache_released(self):
        worker = RenderWorker(self.data["image"],(2350,2200),cache_mb=2,gdal_cache_mb=8,pixel_limit=100_000)
        for step in range(100):
            serial = worker.submit(Camera(1000+step,1000,.3),300,250)
        self.assertEqual(worker.stats()["pending"],1)
        worker.start()
        try:
            deadline=time.monotonic()+10
            result=None
            while result is None and time.monotonic()<deadline:
                result=worker.take_result()
                time.sleep(.01)
            self.assertIsNotNone(result)
            self.assertIsNone(result.error)
            self.assertEqual(result.request.serial,serial)
            self.assertEqual(worker.stats()["reads"],1)
            self.assertLessEqual(worker.stats()["cache_bytes"],2*1024**2)
            worker.clear_cache()
            deadline=time.monotonic()+5
            while worker.stats()["cache_bytes"] and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(worker.stats()["cache_bytes"],0)
        finally:
            worker.stop()
            worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_render_pixel_cap_and_outside_transparency(self):
        with rasterio.open(self.data["image"]) as dataset:
            image=render_region(dataset,RenderRequest(1,1000,1000,10,4000,3000),100_000)
            self.assertLessEqual(image.width*image.height,100_000)
            outside=render_region(dataset,RenderRequest(2,-500,-500,1,100,100),100_000)
            self.assertEqual(outside.getpixel((50,50)),(0,0,0,0))

    def test_old_inflight_frame_is_available_during_new_read(self):
        started, release, second, release_second = (threading.Event() for _ in range(4))
        original = render_region
        def delayed(dataset, request, limit):
            if request.serial==1:
                started.set(); release.wait(5)
            elif request.serial==2:
                second.set(); release_second.wait(5)
            return original(dataset,request,limit)
        worker=RenderWorker(self.data["image"],(2350,2200),cache_mb=2,gdal_cache_mb=8,pixel_limit=100_000)
        with patch("aerial_atlas.mosaic_viewer.render_region", side_effect=delayed):
            worker.start()
            try:
                worker.submit(Camera(1000,1000,.3),300,250)
                self.assertTrue(started.wait(5))
                worker.submit(Camera(1080,1000,.3),300,250)
                release.set()
                self.assertTrue(second.wait(5))
                result=worker.take_result()
                self.assertIsNotNone(result)
                self.assertEqual(result.request.serial,1)
            finally:
                release.set(); release_second.set()
                worker.stop(); worker.join(5)
        self.assertFalse(worker.is_alive())
