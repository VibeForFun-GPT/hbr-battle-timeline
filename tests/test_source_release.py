"""Verify that source releases exclude runtime data and detect corruption."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_rules import allowed, source_files
from tools.make_source_archive import build_archive
from tools.check_release import check_archive


class SourceReleaseTests(unittest.TestCase):
    def test_runtime_data_and_private_assets_are_excluded(self):
        for relative in ("data/jobs/example/axis.json", "data/server.json", "data/character-catalog/catalog.json",
                         "tests/fixtures/bootstrap.jpg", ".env", ".venv/pyvenv.cfg", "axis/__pycache__/vision.pyc",
                         "tools/private.mp4", "web/avatar.webp", "../outside.py"):
            self.assertFalse(allowed(relative), relative)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("axis/model.py", "data/server.json", "data/character-catalog/catalog.json", "tests/fixtures/bootstrap.jpg"):
                target = root / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text("private", encoding="utf-8")
            self.assertEqual([p.relative_to(root).as_posix() for p in source_files(root)], ["axis/model.py"])

    def test_archive_checksums_and_structure(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = build_archive(Path(temporary))
            self.assertEqual(check_archive(archive), [])
            altered = Path(temporary) / "altered.zip"
            with zipfile.ZipFile(archive) as original, zipfile.ZipFile(altered, "w") as destination:
                for item in original.infolist():
                    raw = original.read(item)
                    if item.filename.endswith("/README.md") and item.filename.count("/") == 1:
                        raw += b"\nchanged\n"
                    destination.writestr(item, raw)
            self.assertTrue(any("checksum" in error.lower() for error in check_archive(altered)))


if __name__ == "__main__":
    unittest.main()
