"""The derived index is reused only while its fingerprint matches its sources.   python -m unittest discover -s code/tests -v

Everything runs against a throw-away dataset and cache directory; the real dataset and the real index are never touched.
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ghost.index as index  # noqa: E402


class IndexCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ds = self.tmp / "dataset"
        (self.ds / "evidence").mkdir(parents=True)
        (self.ds / "evidence" / "a.dat").write_bytes(b"first version")      # an extension the builder ignores: only hashed
        self.manifest("file_id,file_path,origin,captured_at\nevidence_01,evidence/a.dat,test,2026-09-01T00:00:00Z\n")
        self.parser = self.tmp / "parser.py"
        self.parser.write_text("RULES = 1\n")
        patches = [mock.patch.object(index, "DATASET", self.ds), mock.patch.object(index, "CACHE_DIR", self.tmp / "cache"),
                   mock.patch.object(index, "INDEX_PATH", self.tmp / "cache" / "index.sqlite"),
                   mock.patch.object(index, "PARSER_SOURCES", (str(self.parser),)),
                   mock.patch.object(index, "prefetch_ocr", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.builds = []
        real = index._build
        patcher = mock.patch.object(index, "_build", lambda fp: (self.builds.append(fp), real(fp))[1])
        patcher.start()
        self.addCleanup(patcher.stop)

    def manifest(self, text):
        (self.ds / "evidence_files.csv").write_text(text, encoding="utf-8")

    def test_matching_fingerprint_reuses_the_index(self):
        index.build_index()
        index.build_index()
        index.connect().close()
        self.assertEqual(len(self.builds), 1)
        self.assertTrue(index.index_is_fresh())
        self.assertEqual(index.stored_fingerprint(), index.fingerprint())

    def test_changed_evidence_file_rebuilds(self):
        index.build_index()
        (self.ds / "evidence" / "a.dat").write_bytes(b"second version")
        self.assertFalse(index.index_is_fresh())
        index.build_index()
        self.assertEqual(len(self.builds), 2)
        self.assertTrue(index.index_is_fresh())

    def test_changed_manifest_rebuilds(self):
        index.build_index()
        self.manifest("file_id,file_path,origin,captured_at\nevidence_01,evidence/a.dat,test,2026-09-02T00:00:00Z\n")
        index.build_index()
        self.assertEqual(len(self.builds), 2)

    def test_changed_parser_code_rebuilds(self):
        index.build_index()
        self.parser.write_text("RULES = 2\n")
        index.build_index()
        self.assertEqual(len(self.builds), 2)
        index.build_index()
        self.assertEqual(len(self.builds), 2)                                # and is then reused again

    def test_changed_schema_rebuilds(self):
        index.build_index()
        with mock.patch.object(index, "SCHEMA", index.SCHEMA + "\ncreate table if not exists extra (x text);\n"):
            self.assertFalse(index.index_is_fresh())
            index.build_index()
        self.assertEqual(len(self.builds), 2)

    def test_changed_index_version_rebuilds(self):
        index.build_index()
        with mock.patch.object(index, "INDEX_VERSION", index.INDEX_VERSION + 1):
            index.build_index()
        self.assertEqual(len(self.builds), 2)

    def test_changed_ocr_model_rebuilds(self):
        index.build_index()
        with mock.patch("ghost.llm.MODEL", "some-other-model"):
            index.build_index()
        self.assertEqual(len(self.builds), 2)

    def test_index_without_a_fingerprint_is_stale(self):
        index.build_index()
        con = sqlite3.connect(index.INDEX_PATH)
        con.execute("delete from meta")                                       # what an old or interrupted build looks like
        con.commit()
        con.close()
        index.build_index()
        self.assertEqual(len(self.builds), 2)

    def test_corrupt_index_file_is_rebuilt(self):
        index.build_index()
        index.INDEX_PATH.write_bytes(b"this is not a sqlite database" * 50)
        index.build_index()
        self.assertEqual(len(self.builds), 2)
        self.assertTrue(index.index_is_fresh())

    def test_force_always_rebuilds(self):
        index.build_index()
        index.build_index(force=True)
        self.assertEqual(len(self.builds), 2)

    def test_the_dataset_is_never_modified(self):
        before = {p: p.read_bytes() for p in self.ds.rglob("*") if p.is_file()}
        index.build_index()
        index.build_index(force=True)
        self.assertEqual({p: p.read_bytes() for p in self.ds.rglob("*") if p.is_file()}, before)


if __name__ == "__main__":
    unittest.main()
