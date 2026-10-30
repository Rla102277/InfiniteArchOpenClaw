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

    def test_symlink_root_rejected_before_any_writes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            destination.mkdir()
            home = root / "workspace"
            home.symlink_to(destination, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "not a symlink"):
                seed(home)
            self.assertTrue(home.is_symlink())
            self.assertEqual(home.readlink(), destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_symlink_children_rejected_before_any_writes(self):
        directories = (
            "knowledge", "projects", "runtime", "approvals", "backups",
            "logs", "inbox", "exports", "projects/first-project",
        )
        files = ("README.md", "knowledge/README.md", "backups/README.md",
                 "projects/first-project/HANDOFF.md")
        for relative in (*directories, *files):
            with self.subTest(relative=relative), TemporaryDirectory() as temporary:
                root = Path(temporary)
                home = root / "workspace"
                home.mkdir()
                destination = root / "destination"
                destination.mkdir()
                sentinel = destination / "owner-notes.txt"
                sentinel.write_text("Existing owner data\n")
                link = home / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                is_directory = relative in directories
                target = destination if is_directory else sentinel
                link.symlink_to(target, target_is_directory=is_directory)
                original_paths = set(home.rglob("*"))
                with self.assertRaisesRegex(ValueError, "symlink"):
                    seed(home)
                self.assertEqual(set(home.rglob("*")), original_paths)
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.readlink(), target)
                self.assertEqual(list(destination.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_text(), "Existing owner data\n")
