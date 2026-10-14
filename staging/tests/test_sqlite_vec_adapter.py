"""Offline stdlib tests for disabled sqlite-vec integration boundary."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "integrations" / "sqlite_vec_adapter.py"
spec = importlib.util.spec_from_file_location("sqlite_vec_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class TestVectorAdapter(unittest.TestCase):
    def test_refuses_unconfigured_extension(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "memory.db"
            with self.assertRaises(adapter.VectorExtensionNotConfigured):
                adapter.connect_vector_db(db)
            self.assertFalse(db.exists())

    def test_rejects_bad_extension(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "fake.txt"
            fake.write_text("not a real extension")
            with self.assertRaises(ValueError):
                adapter.connect_vector_db(Path(d) / "memory.db", fake)

    def test_embedding_validation(self):
        self.assertEqual(adapter.pack_embedding([1, 2.5]), "[1.0,2.5]")
        for bad in ([], [float("nan")], [float("inf")], [True], ["1"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    adapter.pack_embedding(bad)


if __name__ == "__main__":
    unittest.main()
