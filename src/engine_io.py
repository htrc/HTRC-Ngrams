import zipfile
import time
from dataclasses import dataclass, field
from pathlib import Path


class ZipAccessError(RuntimeError):
    def __init__(self, zip_path: Path, detail: str):
        self.zip_path = Path(zip_path)
        self.detail = detail
        super().__init__(f"Could not read ZIP '{self.zip_path}': {detail}")


class CorruptZipError(ZipAccessError):
    pass


class ZipPermissionError(ZipAccessError):
    pass


class ZipReadError(ZipAccessError):
    pass


class ZipNotFoundError(ZipAccessError):
    pass


class MetadataAccessError(RuntimeError):
    def __init__(self, path: Path, detail: str):
        self.path = Path(path)
        self.detail = detail
        super().__init__(f"Could not access metadata at '{self.path}': {detail}")


class MetadataPermissionError(MetadataAccessError):
    pass


@dataclass
class VolumeReadStats:
    total_page_files: int = 0
    yielded_pages: int = 0
    failed_pages: int = 0
    decoding_warnings: int = 0
    io_seconds: float = 0.0
    failed_page_examples: list[str] = field(default_factory=list)
    decoding_warning_examples: list[str] = field(default_factory=list)

    def record_page_failure(self, page_name: str, error: Exception) -> None:
        self.failed_pages += 1
        if len(self.failed_page_examples) < 5:
            self.failed_page_examples.append(
                f"{page_name}: {type(error).__name__}: {error}"
            )

    def record_decoding_warning(self, page_name: str) -> None:
        self.decoding_warnings += 1
        if len(self.decoding_warning_examples) < 5:
            self.decoding_warning_examples.append(page_name)


def htid_to_zip_path(htid: str, pairtree_root: Path) -> Path:
    """
    Convert a HathiTrust ID (HTID) into its exact physical Pairtree Zip path.
    Bypasses file system traversal by computing the path mathematically.
    """
    parts = htid.split('.', 1)
    if len(parts) == 2:
        lib, clean_id = parts
    else:
        parts = htid.split(':', 1)
        if len(parts) == 2:
            lib, clean_id = parts
        else:
            lib, clean_id = "unknown", htid

    encoded_id = clean_id.replace(':', '+').replace('/', '=')
    chunks = [encoded_id[i:i+2] for i in range(0, len(encoded_id), 2)]
    chunk_path = Path("/".join(chunks))
    zip_path = pairtree_root / lib / "pairtree_root" / chunk_path / encoded_id / f"{encoded_id}.zip"
    return zip_path

def htid_to_tsv_path(htid: str, output_root: Path) -> Path:
    """
    Mirror the Pairtree folder structure for output TSV files.
    Produces the identical nested path as htid_to_zip_path()
    but rooted at output_root and with .tsv.gz extension.
    """
    zip_path = htid_to_zip_path(htid, output_root)
    return zip_path.with_suffix('.tsv.gz')

def get_zip_size_mb(zip_path: Path) -> float:
    """Return the compressed ZIP file size in megabytes."""
    try:
        return zip_path.stat().st_size / (1024 * 1024)
    except FileNotFoundError as error:
        raise ZipNotFoundError(zip_path, str(error)) from error
    except PermissionError as error:
        raise ZipPermissionError(zip_path, str(error)) from error
    except OSError as error:
        raise ZipReadError(zip_path, str(error)) from error

def stream_volume_from_zip(zip_path: Path, stats: VolumeReadStats = None):
    """
    Generator that yields one page of text at a time.
    Never holds more than one page in memory — safe for very large volumes.
    The ZIP is opened fresh on each call, so this can be called multiple times
    without conflict (e.g., once for language sampling, once for full processing).
    """
    read_stats = stats if stats is not None else VolumeReadStats()

    try:
        open_started_at = time.perf_counter()
        with zipfile.ZipFile(zip_path, 'r') as zf:
            txt_names = sorted(
                name for name in zf.namelist() if name.endswith('.txt')
            )
            read_stats.io_seconds += time.perf_counter() - open_started_at
            read_stats.total_page_files = len(txt_names)

            for name in txt_names:
                page_started_at = time.perf_counter()
                try:
                    data = zf.read(name)
                except Exception as error:
                    read_stats.record_page_failure(name, error)
                    read_stats.io_seconds += time.perf_counter() - page_started_at
                    continue

                try:
                    page_text = data.decode('utf-8')
                except UnicodeDecodeError:
                    read_stats.record_decoding_warning(name)
                    page_text = data.decode('latin-1')

                read_stats.io_seconds += time.perf_counter() - page_started_at
                read_stats.yielded_pages += 1
                yield page_text
    except CorruptZipError:
        raise
    except zipfile.BadZipFile as error:
        raise CorruptZipError(zip_path, str(error)) from error
    except FileNotFoundError as error:
        raise ZipNotFoundError(zip_path, str(error)) from error
    except PermissionError as error:
        raise ZipPermissionError(zip_path, str(error)) from error
    except OSError as error:
        raise ZipReadError(zip_path, str(error)) from error

def find_jsonld_path(htid: str, ef_root: Path):
    """
    Locate the JSON-LD Bibframe file for a given HTID.
    Uses os.scandir instead of glob to safely handle
    unusual folder names (dollar signs, etc.) in the EF dataset.
    Returns Path if found, None if not found.
    """
    import os

    encoded_htid = htid.replace(':', '+').replace('/', '=')
    lib = htid.split('.')[0] if '.' in htid else htid.split(':')[0]
    filename = f"{encoded_htid}.json.bz2"

    try:
        lib_dir = ef_root / lib
        if not lib_dir.exists():
            return None

        # Scan each batch subfolder looking for our file
        with os.scandir(lib_dir) as batches:
            for batch in batches:
                if not batch.is_dir():
                    continue
                candidate = Path(batch.path) / filename
                if candidate.exists():
                    return candidate
    except PermissionError as error:
        raise MetadataPermissionError(lib_dir, str(error)) from error
    except OSError as error:
        raise MetadataAccessError(lib_dir, str(error)) from error

    return None
