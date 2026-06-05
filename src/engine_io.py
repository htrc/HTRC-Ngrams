import zipfile
from pathlib import Path

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
    but rooted at output_root and with .tsv extension.
    """
    zip_path = htid_to_zip_path(htid, output_root)
    return zip_path.with_suffix('.tsv')

def get_zip_size_mb(zip_path: Path) -> float:
    """Return the compressed ZIP file size in megabytes."""
    return zip_path.stat().st_size / (1024 * 1024)

def stream_volume_from_zip(zip_path: Path):
    """
    Generator that yields one page of text at a time.
    Never holds more than one page in memory — safe for very large volumes.
    The ZIP is opened fresh on each call, so this can be called multiple times
    without conflict (e.g., once for language sampling, once for full processing).
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        txt_names = sorted(name for name in zf.namelist() if name.endswith('.txt'))
        for name in txt_names:
            try:
                data = zf.read(name)
                try:
                    yield data.decode('utf-8')
                except UnicodeDecodeError:
                    yield data.decode('latin-1')
            except Exception:
                pass

def read_volume_from_zip(zip_path: Path):
    """
    Open a volume zip in memory and return (full_text, page_count).
    page_count = number of .txt files found inside the zip.
    """
    pages = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        txt_names = sorted(name for name in zf.namelist() if name.endswith('.txt'))
        for name in txt_names:
            try:
                data = zf.read(name)
                try:
                    pages.append(data.decode('utf-8'))
                except UnicodeDecodeError:
                    pages.append(data.decode('latin-1'))
            except Exception:
                pass
    return pages
