import os
import json
import uuid
import tempfile
from pathlib import Path
from unittest import TestCase, main

from src.engine_export import is_complete_tsv, atomic_write_tsv
from src.provenance import validate_existing_provenance
from run_pipeline import validate_run_metadata, repair_truncated_log

class TestTask6IO(TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.test_dir.name)
        
    def tearDown(self):
        self.test_dir.cleanup()

    def test_is_complete_tsv_valid(self):
        file_path = self.tmp_path / "valid.tsv"
        with open(file_path, "wb") as f:
            f.write(b"w1\tw2\tfreq\tpmi\n")
            f.write(b"hello\tworld\t10\t1.5\n")
        self.assertTrue(is_complete_tsv(file_path))

    def test_is_complete_tsv_truncated_row(self):
        file_path = self.tmp_path / "truncated.tsv"
        with open(file_path, "wb") as f:
            f.write(b"w1\tw2\tfreq\tpmi\n")
            f.write(b"hello\tworld\t10\t")
        # Missing newline at the end!
        self.assertFalse(is_complete_tsv(file_path))
        
    def test_is_complete_tsv_header_only(self):
        file_path = self.tmp_path / "header_only.tsv"
        with open(file_path, "wb") as f:
            f.write(b"w1\tw2\tfreq\tpmi\n")
        self.assertTrue(is_complete_tsv(file_path))

    def test_is_complete_tsv_invalid_header(self):
        file_path = self.tmp_path / "invalid_header.tsv"
        with open(file_path, "wb") as f:
            f.write(b"w1\tw2\tfreq\tpmi\tEXTRA\n")
            f.write(b"hello\tworld\t10\t1.5\tbad\n")
        self.assertFalse(is_complete_tsv(file_path))

    def test_atomic_write_tsv_success(self):
        file_path = self.tmp_path / "atomic_success.tsv"
        headers = ["w1", "w2", "freq"]
        rows = [["a", "b", 1], ["c", "d", 2]]
        
        atomic_write_tsv(file_path, headers, rows)
        self.assertTrue(file_path.exists())
        self.assertTrue(is_complete_tsv(file_path))
        
        # Check no .tmp files were left behind
        tmp_files = list(self.tmp_path.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_atomic_write_tsv_generator(self):
        file_path = self.tmp_path / "atomic_gen.tsv"
        headers = ["w1", "w2", "freq"]
        def row_generator():
            yield ["x", "y", 3]
            yield ["z", "w", 4]
            
        atomic_write_tsv(file_path, headers, row_generator())
        self.assertTrue(file_path.exists())
        self.assertTrue(is_complete_tsv(file_path))

    def test_atomic_write_tsv_failure_cleanup(self):
        file_path = self.tmp_path / "atomic_fail.tsv"
        headers = ["w1", "w2", "freq"]
        def bad_generator():
            yield ["x", "y", 3]
            raise ValueError("Simulated crash mid-write")
            
        with self.assertRaises(ValueError):
            atomic_write_tsv(file_path, headers, bad_generator())
            
        # The final file should not exist
        self.assertFalse(file_path.exists())
        # The .tmp file should have been cleaned up
        tmp_files = list(self.tmp_path.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_custom_tsv_headers(self):
        file_path = self.tmp_path / "custom_header.tsv"
        headers = ["custom1", "custom2", "custom3"]
        rows = [["a", "b", "c"], ["d", "e", "f"]]
        atomic_write_tsv(file_path, headers, rows)
        self.assertTrue(file_path.exists())
        self.assertTrue(is_complete_tsv(file_path, allowed_headers={("custom1", "custom2", "custom3")}))

    def test_corrupt_provenance(self):
        file_path = self.tmp_path / "prov.json"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("Not a JSON object")
        with self.assertRaises(RuntimeError):
            validate_existing_provenance(file_path, {"run_id": "test_run"})
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.write('{"run_id": "wrong_run"}')
        with self.assertRaises(RuntimeError):
            validate_existing_provenance(file_path, {"run_id": "test_run"})

    def test_manifest_conflicts(self):
        file_path = self.tmp_path / "run_metadata.json"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write('{"min_freq": 2, "filter1": true}')
            
        with self.assertRaises(RuntimeError):
            validate_run_metadata(file_path, {"min_freq": 3, "filter1": True})
            
        # Should pass if exactly the same
        validate_run_metadata(file_path, {"min_freq": 2, "filter1": True})

    def test_repair_truncated_log(self):
        file_path = self.tmp_path / "task.csv"

        with open(file_path, "wb") as file:
            file.write(b"header\n")
            file.write(b"complete\n")
            file.write(b"partial")

        repair_truncated_log(file_path)

        self.assertEqual(
            file_path.read_bytes(),
            b"header\ncomplete\n",
        )

if __name__ == "__main__":
    main()
