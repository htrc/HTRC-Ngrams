import csv
import os
import time
import uuid
from pathlib import Path
from typing import Iterable, Union


BIGRAM_HEADERS = {
    ("w1", "w2", "freq"),
}

def is_complete_tsv(path: Path, allowed_headers=BIGRAM_HEADERS) -> bool:
    """
    Validates whether a TSV file is fully written and well-formed.
    Verifies the exact header matches and the final byte is a newline.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return False

    try:
        if allowed_headers is not None:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.reader(f, delimiter='\t')
                header = next(reader, None)
                if header is None:
                    return False
                if tuple(header) not in allowed_headers:
                    return False

        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b'\n':
                return False

        return True
    except Exception:
        return False

def atomic_write_tsv(filepath: Union[str, Path], headers: Iterable[str], rows: Iterable[Iterable[Union[str, int, float]]]):
    """
    Writes data to a temporary TSV file, flushes to disk, validates, and atomically renames.
    Accepts a generator for `rows` to avoid large in-memory copies.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    headers = tuple(headers)

    pid = os.getpid()
    uid = uuid.uuid4().hex[:8]
    tmp_path = filepath.with_name(f"{filepath.name}.{pid}_{uid}.tmp")
    total_started_at = time.perf_counter()
    write_seconds = 0.0
    fsync_seconds = 0.0
    validation_seconds = 0.0
    replace_seconds = 0.0

    try:
        write_started_at = time.perf_counter()
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)

            f.flush()
            write_seconds = time.perf_counter() - write_started_at

            fsync_started_at = time.perf_counter()
            os.fsync(f.fileno())
            fsync_seconds = time.perf_counter() - fsync_started_at

        validation_started_at = time.perf_counter()
        if not is_complete_tsv(tmp_path, allowed_headers={headers}):
            raise RuntimeError(f"Generated temporary file {tmp_path} is missing final newline or empty.")
        validation_seconds = time.perf_counter() - validation_started_at

        replace_started_at = time.perf_counter()
        os.replace(tmp_path, filepath)
        replace_seconds = time.perf_counter() - replace_started_at
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return {
        "export_seconds": time.perf_counter() - total_started_at,
        "export_write_seconds": write_seconds,
        "export_fsync_seconds": fsync_seconds,
        "export_validation_seconds": validation_seconds,
        "export_replace_seconds": replace_seconds,
    }
