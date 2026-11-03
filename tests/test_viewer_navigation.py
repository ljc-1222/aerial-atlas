"""History branching, bounded metadata, and safe persistent camera validation."""




import unittest
from aerial_atlas.viewer_navigation import ViewHistory, ViewState, validated_saved_view


class NavigationTests(unittest.TestCase):
    def test_back_forward_restores_complete_state(self):
        history = ViewHistory()
        a = ViewState(100, 200, .5, "a.png", 4, "04_")
        b = ViewState(300, 400, 1, "b.png", 5, "")
        history.record(a)
        history.record(b)
        self.assertEqual(history.step(-1), a)
        self.assertEqual(history.step(1), b)
        self.assertIsNone(history.step(1))

    def test_new_action_after_back_discards_forward_branch(self):
        history = ViewHistory()
        for x in (1, 2, 3):
            history.record(ViewState(x, 0, 1))
        history.step(-1)
        history.record(ViewState(4, 0, 1))
        self.assertFalse(history.can_forward)
        self.assertEqual(history.step(-1).cx, 2)

    def test_duplicate_and_roundoff_states_do_not_grow_history(self):
        history = ViewHistory()
        history.record(ViewState(10, 20, .5))
        for _ in range(100):
            history.record(ViewState(10 + 1e-8, 20, .5 + 1e-12))
        self.assertEqual(len(history.entries), 1)

    def test_history_has_a_fixed_limit(self):
        history = ViewHistory(64)
        for x in range(1000):
            history.record(ViewState(x, 0, 1))
        self.assertEqual(len(history.entries), 64)
        self.assertEqual(history.entries[0].cx, 936)
        self.assertEqual(history.position, 63)

    def test_invalid_camera_data_is_rejected(self):
        for data in ({}, dict(cx=True, cy=0, scale=1), dict(cx=float("nan"), cy=0, scale=1),
                     dict(cx=10**1000, cy=0, scale=1),
                     dict(cx=0, cy=0, scale=0), dict(cx=0, cy=float("inf"), scale=1),
                     dict(cx=0, cy=0, scale=1, row="x")):
            self.assertIsNone(ViewState.from_dict(data))
        state = ViewState.from_dict(dict(cx=100, cy=200, scale=.3, query="x" * 10000))
        self.assertEqual(len(state.query), 512)

    def test_saved_view_requires_a_map_identity(self):
        state = ViewState(100, 200, .5).to_dict()
        self.assertIsNone(validated_saved_view(dict(state=state)))
        saved = dict(state=state, map_key="map.tif", extent=[300, 400])
        self.assertEqual(validated_saved_view(saved), saved)
        self.assertIsNone(validated_saved_view(dict(state=state, map_key="map.tif", extent=[True, 400])))


if __name__ == "__main__":
    unittest.main()
