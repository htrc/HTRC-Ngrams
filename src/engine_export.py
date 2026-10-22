import csv
import gzip
import io
import json
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
    Verifies the exact header. Atomic publication guarantees that gzip outputs
    are only visible after the compressed stream has closed and reached disk.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return False

    try:
        is_gzip = path.name.endswith(".gz")
        text_opener = gzip.open if is_gzip else open

        if is_gzip:
            with open(path, "rb") as raw_file:
                if raw_file.read(2) != b"\x1f\x8b":
                    return False

        if allowed_headers is not None:
            with text_opener(
                path,
                "rt",
                encoding="utf-8",
                newline="",
            ) as f:
                reader = csv.reader(f, delimiter='\t')
                header = next(reader, None)
                if header is None:
                    return False
                if tuple(header) not in allowed_headers:
                    return False

        if not is_gzip:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b'\n':
                    return False

        return True
    except Exception:
        return False

def atomic_write_tsv(
    filepath: Union[str, Path],
    headers: Iterable[str],
    rows: Iterable[Iterable[Union[str, int, float]]],
    gzip_level: int = 1,
):
    """
    Writes data to a temporary TSV file, flushes to disk, validates, and atomically renames.
    Paths ending in .gz are compressed directly without creating an uncompressed copy.
    Accepts a generator for `rows` to avoid large in-memory copies.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    headers = tuple(headers)

    pid = os.getpid()
    uid = uuid.uuid4().hex[:8]
    if filepath.name.endswith(".gz"):
        tmp_path = filepath.with_name(
            f"{filepath.name}.{pid}_{uid}.tmp.gz"
        )
    else:
        tmp_path = filepath.with_name(f"{filepath.name}.{pid}_{uid}.tmp")
    total_started_at = time.perf_counter()
    write_seconds = 0.0
    fsync_seconds = 0.0
    validation_seconds = 0.0
    replace_seconds = 0.0

    try:
        write_started_at = time.perf_counter()
        if filepath.name.endswith(".gz"):
            if not 0 <= gzip_level <= 9:
                raise ValueError("gzip_level must be between 0 and 9")
            with open(tmp_path, "wb") as raw_file:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw_file,
                    compresslevel=gzip_level,
                    mtime=0,
                ) as gzip_file:
                    with io.TextIOWrapper(
                        gzip_file,
                        encoding="utf-8",
                        newline="",
                    ) as text_file:
                        writer = csv.writer(
                            text_file,
                            delimiter="\t",
                            lineterminator="\n",
                        )
                        writer.writerow(headers)
                        writer.writerows(rows)
                raw_file.flush()
                write_seconds = time.perf_counter() - write_started_at

                fsync_started_at = time.perf_counter()
                os.fsync(raw_file.fileno())
                fsync_seconds = time.perf_counter() - fsync_started_at
        else:
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
        if not is_complete_tsv(tmp_path, allowed_headers={tuple(headers)}):
            raise RuntimeError(
                f"Generated temporary file failed TSV validation: {tmp_path}"
            )
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


def atomic_write_json(filepath: Union[str, Path], payload) -> Path:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = filepath.with_name(
        f"{filepath.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
    )

    try:
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, filepath)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return filepath
