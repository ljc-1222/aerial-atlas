"""Durable preferences, invalid-file recovery, and locale/theme resources."""




from dataclasses import asdict
import json
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch

from aerial_atlas.viewer_preferences import Preferences, SettingsStore, STRINGS, THEMES


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SettingsStore(Path(self.folder.name) / "config" / "settings.json")

    def test_preferences_roundtrip_and_missing_file(self):
        self.assertEqual(self.store.load(), Preferences())
        preferences = Preferences(language="en", theme="dark", show_coordinates=True, show_details=True,
                                  last_tile="demo_03_04.png", window_width=1200)
        self.store.save(preferences)
        self.assertEqual(self.store.load(), preferences)

    def test_bad_json_and_wrong_types_fall_back_without_crashing(self):
        self.store.path.parent.mkdir()
        for text in ("{broken", "null", "[]", '["language"]'):
            self.store.path.write_text(text, encoding="utf-8")
            self.assertEqual(self.store.load(), Preferences())
        data = {"theme": [], "language": "unknown", "remember_view": "false", "show_minimap": 0,
                "window_width": True, "window_height": -1, "last_tile": "a" * 300, "ignored": 9}
        self.store.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.store.load(), Preferences())

    def test_interrupted_replace_preserves_previous_settings(self):
        original = Preferences(theme="dark")
        self.store.save(original)
        with patch("aerial_atlas.viewer_preferences.os.replace", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                self.store.save(Preferences(language="en"))
        self.assertEqual(self.store.load(), original)
        self.assertFalse(list(self.store.path.parent.glob("*.tmp")))

    def test_survey_views_roundtrip_and_reject_unknown_or_invalid_entries(self):
        view = dict(state=dict(cx=10, cy=20, scale=1, selected="1150618_tile_0_0.png", row=0, query=""),
                    map_key="survey-map", extent=[2048, 3072])
        preferences = Preferences(last_dataset="1150618", dataset_views={"1150618": view})
        self.store.save(preferences)
        self.assertEqual(self.store.load(), preferences)
        data = asdict(preferences)
        data["last_dataset"] = "../other"
        data["dataset_views"].update({"../other": view, "1150806": {"bad": True}})
        self.store.path.write_text(json.dumps(data), encoding="utf-8")
        restored = self.store.load()
        self.assertEqual(restored.last_dataset, "1150427")
        self.assertEqual(restored.dataset_views, {"1150618": view})

    def test_translations_have_matching_keys_and_format_fields(self):
        chinese, english = STRINGS["zh-TW"], STRINGS["en"]
        self.assertEqual(chinese.keys(), english.keys())
        formatter = string.Formatter()
        for key in chinese:
            fields = lambda text: {name for _, name, _, _ in formatter.parse(text) if name is not None}
            self.assertEqual(fields(chinese[key]), fields(english[key]), key)

    def test_text_contrast_in_both_themes(self):
        def luminance(hex_color):
            channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
        for name, theme in THEMES.items():
            for foreground, background in (("ink", "surface"), ("muted", "surface"), ("on_accent", "accent"), ("error", "surface")):
                a, b = sorted((luminance(theme[foreground]), luminance(theme[background])))
                self.assertGreaterEqual((b + .05) / (a + .05), 4.5, (name, foreground, background))


if __name__ == "__main__":
    unittest.main()
