from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import json
from aerial_atlas.paths import atomic_json


class AtomicRecordTests(unittest.TestCase):
    def test_transient_windows_sharing_violation_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"record.json"
            atomic_json(path,{"old":True})
            original=os.replace
            calls=[]
            def replace(source,target):
                calls.append(1)
                if len(calls)==1:
                    raise PermissionError("sharing violation")
                return original(source,target)
            with patch("aerial_atlas.paths.os.replace",side_effect=replace):
                atomic_json(path,{"new":True})
            self.assertEqual(json.loads(path.read_text()),{"new":True})
            self.assertEqual(len(calls),2)

    def test_persistent_failure_preserves_previous_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"record.json"
            atomic_json(path,{"old":True})
            with patch("aerial_atlas.paths.os.replace",side_effect=PermissionError("read only")), patch("aerial_atlas.paths.time.sleep"):
                with self.assertRaises(PermissionError):
                    atomic_json(path,{"new":True})
            self.assertEqual(json.loads(path.read_text()),{"old":True})
            self.assertEqual(list(Path(folder).glob("*.tmp")),[])
