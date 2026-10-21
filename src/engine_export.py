import csv
import os
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

    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t", lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)

            f.flush()
            os.fsync(f.fileno())

        if not is_complete_tsv(tmp_path, allowed_headers={headers}):
            raise RuntimeError(f"Generated temporary file {tmp_path} is missing final newline or empty.")

        os.replace(tmp_path, filepath)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
