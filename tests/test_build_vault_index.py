import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "build_vault_index", REPOSITORY_ROOT / "hooks" / "build_vault_index.py"
)
build_vault_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_vault_index)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        self.db = str(Path(self.tmp.name) / "index.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_build(self, *extra):
        argv = ["build_vault_index.py", "--vault", str(self.vault), "--db", self.db, *extra]
        with mock.patch.object(build_vault_index, "embed", return_value=b"\0\0\0\0"), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch("sys.stdout"):
            build_vault_index.main()

    def indexed_paths(self):
        with sqlite3.connect(self.db) as conn:
            return {row[0] for row in conn.execute("SELECT DISTINCT path FROM embeddings")}

    def test_incremental_run_drops_deleted_notes(self):
        (self.vault / "keep.md").write_text("keep")
        (self.vault / "gone.md").write_text("gone")
        self.run_build()
        (self.vault / "gone.md").unlink()
        self.run_build("--incremental")
        self.assertEqual(self.indexed_paths(), {"keep.md"})

    def test_incremental_run_drops_case_only_renames(self):
        (self.vault / "Note.md").write_text("x")
        self.run_build()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO embeddings(path, mtime, chunk_index, chunk_text, embedding)"
                " VALUES ('note.md', 0, 0, 'x', x'00000000')"
            )
        self.run_build("--incremental")
        self.assertEqual(self.indexed_paths(), {"Note.md"})

    def test_empty_vault_does_not_wipe_index(self):
        (self.vault / "keep.md").write_text("keep")
        self.run_build()
        (self.vault / "keep.md").unlink()
        self.run_build("--incremental")
        self.assertEqual(self.indexed_paths(), {"keep.md"})


if __name__ == "__main__":
    unittest.main()
