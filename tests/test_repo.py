"""Publishing checks must inspect Git's index, including force-added private files."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from check_repo import audit


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        (self.root/"README.md").write_text("# Example\n")
        (self.root/"images").mkdir()
        (self.root/"images/.gitkeep").touch()

    def git(self,*args):
        return subprocess.run(["git",*args],cwd=self.root,check=True,capture_output=True)

    def test_force_added_private_image_is_rejected(self):
        self.git("init","--quiet")
        self.git("add","README.md","images/.gitkeep")
        audit(self.root)
        source=self.root/"images/1150427/RGB_png"
        source.mkdir(parents=True)
        (source/"private.png").write_bytes(b"private input")
        self.git("add","-f","images/1150427/RGB_png/private.png")
        with self.assertRaisesRegex(ValueError,"Unapproved.*private.png"):
            audit(self.root)

    def test_unapproved_root_file_is_rejected(self):
        self.git("init","--quiet")
        (self.root/"local-record.json").write_text('{}')
        self.git("add","local-record.json")
        with self.assertRaisesRegex(ValueError,"Unapproved.*local-record.json"):
            audit(self.root)

    def test_git_free_source_archive_can_be_checked(self):
        self.assertEqual(len(audit(self.root)),2)
