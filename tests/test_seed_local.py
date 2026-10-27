from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from scripts.seed_local import seed

class SeedTests(TestCase):
    def test_seeding_is_idempotent(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            self.assertEqual(seed(p),(9,0))
            fn=p/'projects/first-project/PROJECT.md'
            fn.write_text('USER WORK')
            self.assertEqual(seed(p),(0,9))
            self.assertEqual(fn.read_text(),'USER WORK')
    def test_creates_working_directories(self):
        with TemporaryDirectory() as d:
            seed(Path(d))
            for name in ('knowledge','projects','logs','inbox','backups','runtime'):
                self.assertTrue((Path(d)/name).is_dir())
