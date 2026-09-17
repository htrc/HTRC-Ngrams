import argparse
import csv
import os
import time
import itertools
from pathlib import Path
from collections import Counter
from multiprocessing import Pool

from src.engine_io import (
    CorruptZipError,
    MetadataAccessError,
    MetadataPermissionError,
    VolumeReadStats,
    ZipPermissionError,
    ZipReadError,
    get_zip_size_mb,
    htid_to_tsv_path,
    htid_to_zip_path,
    stream_volume_from_zip,
)
from src.engine_nlp import (
    LowConfidenceLanguageError,
    StanzaPipelineInitializationError,
    UnsupportedLanguageResolutionError,
    UnsupportedStanzaLanguageError,
    generate_ngrams_with_counts,
    get_profile,
    resolve_language,
    tokenize_page,
)
from src.engine_filters import select_top_standard_stopwords, filter_1_standard_stopword_bigrams, filter2_min_frequency, filter3_pmi, UnsupportedLanguageError, get_stopword_metadata
from src.metrics import singleton_rate
from src.provenance import generate_run_provenance, write_run_provenance_once
from src.engine_export import is_complete_tsv, atomic_write_tsv
import json
import sys


class PendingVolumeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


def repair_truncated_log(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return

    with open(path, "rb+") as file:
        file.seek(-1, os.SEEK_END)

        if file.read(1) == b"\n":
            return

        position = file.tell() - 1

        while position >= 0:
            file.seek(position)

            if file.read(1) == b"\n":
                file.truncate(position + 1)
                file.flush()
                os.fsync(file.fileno())
                return

            position -= 1

        file.truncate(0)
        file.flush()
        os.fsync(file.fileno())


def sanitize_log_field(value, max_length=None) -> str:
    sanitized = (
        str(value)
        .replace("\t", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    if max_length is not None and len(sanitized) > max_length:
        return sanitized[:max_length] + "..."
    return sanitized


RESULT_FIELDS = [
    "volume_name",
    "zip_path",
    "language",
    "stopword_source",
    "stopword_list_code",
    "selected_stopword_count",
    "filter_1_enabled",
    "filter_2_enabled",
    "filter_3_enabled",
    "output_file",
    "top25_stopword_file",
    "dropped_bigram_file",
    "total_pages",
    "source_text_pages",
    "failed_pages",
    "decoding_warnings",
    "total_tokens",
    "unique_unigrams",
    "distinct_bigram_types",
    "total_bigram_occurrences",
    "singleton_percentage",
    "tsv_size_bytes",
    "tsv_size_mb",
    "htid",
    "run_id",
    "record_status",
]

PENDING_FIELDS = ["htid", "reason_code", "detail"]

# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def analyze_volume(htid: str, zip_path: Path, output_dir: Path, audit_dir: Path,
                   enable_f1: bool, enable_f2: bool, enable_f3: bool,
                   min_freq: int = 2, min_pmi: float = 0.0, max_exposure: float = 0.15,
                   max_zip_size_mb: float = 150.0, flat_output: bool = False,
                   ef_root: Path = None):
    print(f"\nAnalyzing: {htid}")

    if flat_output:
        safe_name = htid.replace('/', '=').replace(':', '+')
        tsv_path = output_dir / f"{safe_name}.tsv"
    else:
        # Pairtree-mirrored output structure
        tsv_path = htid_to_tsv_path(htid, output_dir)
    volume_name = tsv_path.stem

    # Checkpointing: skip if this volume was already processed in a previous run
    if is_complete_tsv(tsv_path):
        print(f"  ⏭️  SKIP (already done): {volume_name}")
        return {
            "volume_name": volume_name,
            "output_file": str(tsv_path),
            "record_status": "checkpointed_existing",
        }
    elif tsv_path.exists():
        print(f"  ⚠️  WARNING: Found incomplete or corrupted TSV {volume_name}. Overwriting.")

    # Size guard: log and skip volumes whose ZIP exceeds safe memory threshold.
    zip_size_mb = get_zip_size_mb(zip_path)
    if zip_size_mb > max_zip_size_mb:
        print(f"  ⚠️  OVERSIZED ({zip_size_mb:.1f} MB): {volume_name}")
        raise PendingVolumeError(
            "ZIP_TOO_LARGE",
            f"Compressed ZIP is {zip_size_mb:.1f} MiB; limit is {max_zip_size_mb:.1f} MiB.",
        )

    # --- LANGUAGE DETECTION ---
    # Fetch 61 pages so resolve_language() can sample interior pages 40-60
    sample_read_stats = VolumeReadStats()
    sample_pages = list(
        itertools.islice(
            stream_volume_from_zip(zip_path, stats=sample_read_stats),
            61,
        )
    )
    if sample_read_stats.total_page_files == 0:
        raise PendingVolumeError(
            "NO_TEXT_PAGES",
            "The ZIP contains no .txt page files.",
        )
    if not sample_pages and sample_read_stats.failed_pages:
        failure_example = (
            f" First failure: {sample_read_stats.failed_page_examples[0]}"
            if sample_read_stats.failed_page_examples
            else ""
        )
        raise PendingVolumeError(
            "NO_READABLE_PAGES",
            f"All {sample_read_stats.failed_pages} sampled text pages failed."
            f"{failure_example}",
        )
    if sample_read_stats.failed_pages:
        print(
            "  ⚠️  Language sample page-read failures: "
            f"{sample_read_stats.failed_pages:,}"
        )
        for failure in sample_read_stats.failed_page_examples:
            print(f"      {failure}")
    if sample_read_stats.decoding_warnings:
        print(
            "  ⚠️  Language sample Latin-1 decoding fallbacks: "
            f"{sample_read_stats.decoding_warnings:,}"
        )
    lang = resolve_language(htid, sample_pages, ef_root=ef_root)
    profile = get_profile(lang)

    # --- STREAMING PROCESSING PASS ---
    unigram_counts = Counter()
    bigram_counts  = Counter()
    total_tokens   = 0
    page_count     = 0
    read_stats = VolumeReadStats()

    for page in stream_volume_from_zip(zip_path, stats=read_stats):
        page_count += 1
        words = tokenize_page(page, profile, lang)
        if not words:
            continue
        total_tokens += len(words)
        unigram_counts.update(words)
        bigram_counts.update(generate_ngrams_with_counts(words, 2))

    if read_stats.failed_pages:
        print(f"  ⚠️  Failed page reads: {read_stats.failed_pages:,}")
        for failure in read_stats.failed_page_examples:
            print(f"      {failure}")

    if read_stats.decoding_warnings:
        print(
            "  ⚠️  Latin-1 decoding fallbacks: "
            f"{read_stats.decoding_warnings:,}"
        )
        if read_stats.decoding_warning_examples:
            examples = ", ".join(read_stats.decoding_warning_examples)
            print(f"      Example pages: {examples}")

    if total_tokens == 0:
        print(f"  ⚠️  WARNING: No words found in {volume_name}")
        if read_stats.total_page_files == 0:
            raise PendingVolumeError(
                "NO_TEXT_PAGES",
                "The ZIP contains no .txt page files.",
            )
        if read_stats.yielded_pages == 0 and read_stats.failed_pages:
            failure_example = (
                f" First failure: {read_stats.failed_page_examples[0]}"
                if read_stats.failed_page_examples
                else ""
            )
            raise PendingVolumeError(
                "NO_READABLE_PAGES",
                f"All {read_stats.failed_pages} text pages failed during ZIP reading."
                f"{failure_example}",
            )
        raise PendingVolumeError(
            "NO_TOKENS_FOUND",
            "Text pages were read, but tokenization produced no tokens.",
        )

    print(f"  Total readable pages: {page_count:,}")
    print(f"  Source text pages:    {read_stats.total_page_files:,}")
    print(f"  Total tokens: {total_tokens:,}")

    original_total_bigrams = sum(bigram_counts.values())

    # --- APPLY FILTER 1 ---
    stopword_meta = get_stopword_metadata(lang)
    dropped_f1 = Counter()
    selected_stopwords = set()
    top25_with_counts = {}

    top25_dir = audit_dir / "top25_stopwords"
    dropped_dir = audit_dir / "top25_dropped_bigrams"
    top25_dir.mkdir(parents=True, exist_ok=True)
    dropped_dir.mkdir(parents=True, exist_ok=True)
    clean_htid = htid.replace('/', '_').replace(':', '_')
    top25_file_path = str(top25_dir / f"{clean_htid}_top25.txt")
    dropped_file_path = str(dropped_dir / f"{clean_htid}_dropped.tsv")

    if enable_f1:
        original_bigram_count = len(bigram_counts)
        selected_stopwords, top25_with_counts = select_top_standard_stopwords(unigram_counts, lang, top_n=25)
        bigram_counts, dropped_f1 = filter_1_standard_stopword_bigrams(bigram_counts, selected_stopwords)
        print(f"  Filter 1 (Standard Stopwords): Dropped {original_bigram_count - len(bigram_counts):,} stopword-stopword bigrams.")

        # --- EXPORT AUDIT LOGS FOR FILTER 1 ---
        atomic_write_tsv(
            top25_file_path,
            ["word", "freq"],
            top25_with_counts.items()
        )
        atomic_write_tsv(
            dropped_file_path,
            ["w1", "w2", "freq"],
            ([w1, w2, count] for (w1, w2), count in dropped_f1.most_common())
        )
    else:
        print("  Filter 1: SKIPPED (disabled via switch)")


    # --- APPLY FILTER 2 ---
    if enable_f2:
        bigram_counts, rem_dist, rem_occ = filter2_min_frequency(bigram_counts, min_freq=min_freq)
        print(f"  Filter 2 (Min Freq >= {min_freq}): Dropped {rem_dist:,} distinct bigrams; retained {len(bigram_counts):,} distinct bigrams.")
    else:
        print("  Filter 2: SKIPPED (disabled via switch)")

    # --- APPLY FILTER 3 ---
    if enable_f3:
        f2_survivor_count = len(bigram_counts)
        scored_results, pmi_dropped = filter3_pmi(bigram_counts, unigram_counts, original_total_bigrams, total_tokens, min_pmi=min_pmi)
        print(f"  Filter 3 (PMI >= {min_pmi}): Evaluated {f2_survivor_count:,} bigrams; dropped {pmi_dropped:,} rows below the threshold; retained {len(scored_results):,} bigrams.")
        has_pmi = True
        final_results = scored_results
    else:
        print("  Filter 3: SKIPPED (disabled via switch)")
        has_pmi = False
        final_results = bigram_counts.most_common()

    # Export securely formatted filename with atomic writes
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    if has_pmi:
        headers = ["w1", "w2", "freq", "pmi"]
        rows = ([w1, w2, b_count, pmi] for (w1, w2), b_count, pmi in final_results)
    else:
        headers = ["w1", "w2", "freq"]
        rows = ([w1, w2, b_count] for (w1, w2), b_count in final_results)

    atomic_write_tsv(tsv_path, headers, rows)
    print(f"  Exported TSV: {tsv_path.name} ({len(final_results):,} records)")
    tsv_size_bytes = tsv_path.stat().st_size
    tsv_size_mb = tsv_size_bytes / (1024 * 1024)

    if has_pmi:
        final_bigram_counts = Counter(
            {bigram: count for bigram, count, _ in final_results}
        )
    else:
        final_bigram_counts = Counter(
            {bigram: count for bigram, count in final_results}
        )

    singleton_percentage = singleton_rate(final_bigram_counts)

    if has_pmi:
        total_bigram_occurrences = sum(
            b_count for _, b_count, _ in final_results
        )
    else:
        total_bigram_occurrences = sum(
            b_count for _, b_count in final_results
        )

    distinct_bigram_types = len(final_results)
    print(f"  Distinct bigram types:     {distinct_bigram_types:,}")
    print(f"  Total bigram occurrences: {total_bigram_occurrences:,}")
    print(f"  Singleton percentage:     {singleton_percentage:.1f}%")
    print(
        f"  Actual TSV size:           {tsv_size_bytes:,} bytes "
        f"({tsv_size_mb:.3f} MiB)"
    )

    return {
        'volume_name':          volume_name,
        'zip_path':             str(zip_path),
        'language':             lang,
        'stopword_source':      stopword_meta['source'],
        'stopword_list_code':   stopword_meta['list_code'],
        'selected_stopword_count': len(selected_stopwords),
        'filter_1_enabled':     enable_f1,
        'filter_2_enabled':     enable_f2,
        'filter_3_enabled':     enable_f3,
        'output_file':          str(tsv_path),
        'top25_stopword_file':  top25_file_path if enable_f1 else "",
        'dropped_bigram_file':  dropped_file_path if enable_f1 else "",
        'total_pages':          page_count,
        'source_text_pages':    read_stats.total_page_files,
        'failed_pages':         read_stats.failed_pages,
        'decoding_warnings':    read_stats.decoding_warnings,
        'total_tokens':         total_tokens,
        'unique_unigrams':      len(unigram_counts),
        'distinct_bigram_types': distinct_bigram_types,
        'total_bigram_occurrences': total_bigram_occurrences,
        'singleton_percentage': round(singleton_percentage, 1),
        'tsv_size_bytes':       tsv_size_bytes,
        'tsv_size_mb':          round(tsv_size_mb, 3),
    }

# -------------------------------------------------------------------------------------------
# Main (HPC Entrypoint)
# ---------------------------------------------------------------------------

def process_one(args_tuple):
    """Module-level wrapper to avoid multiprocessing PicklingError on local functions."""
    htid, pairtree_root, ef_root, export_dir, audit_dir, enable_f1, enable_f2, enable_f3, min_freq, min_pmi, max_exposure, max_zip_size_mb, flat_output = args_tuple
    zip_path = htid_to_zip_path(htid, pairtree_root)

    def pending(reason_code, detail=""):
        return {
            'type': 'pending',
            'htid': htid,
            'reason_code': reason_code,
            'detail': detail,
        }

    if not zip_path.exists():
        print(f"  ❌ ZIP not found: {htid}")
        return pending("ZIP_NOT_FOUND", str(zip_path))

    try:
        result = analyze_volume(htid, zip_path, export_dir, audit_dir,
                                enable_f1=enable_f1,
                                enable_f2=enable_f2,
                                enable_f3=enable_f3,
                                min_freq=min_freq,
                                min_pmi=min_pmi,
                                max_exposure=max_exposure,
                                max_zip_size_mb=max_zip_size_mb,
                                flat_output=flat_output,
                                ef_root=ef_root)
        result['htid'] = htid
        return {'type': 'success', 'data': result}
    except PendingVolumeError as error:
        print(f"  ⚠️ SKIPPED {htid}: {error}")
        return pending(error.reason_code, error.detail)
    except CorruptZipError as error:
        print(f"  ❌ CORRUPT ZIP {htid}: {error.detail}")
        return pending("ZIP_CORRUPT", error.detail)
    except ZipPermissionError as error:
        print(f"  ❌ ZIP PERMISSION ERROR {htid}: {error.detail}")
        return pending("ZIP_PERMISSION_DENIED", error.detail)
    except ZipReadError as error:
        print(f"  ❌ ZIP READ ERROR {htid}: {error.detail}")
        return pending("ZIP_READ_ERROR", error.detail)
    except MetadataPermissionError as error:
        print(f"  ❌ METADATA PERMISSION ERROR {htid}: {error.detail}")
        return pending("METADATA_PERMISSION_DENIED", error.detail)
    except MetadataAccessError as error:
        print(f"  ❌ METADATA ACCESS ERROR {htid}: {error.detail}")
        return pending("METADATA_ACCESS_ERROR", error.detail)
    except LowConfidenceLanguageError as e:
        print(f"  🔍 SKIPPED {htid}: {e}")
        return pending("LOW_LANGUAGE_CONFIDENCE", str(e))
    except UnsupportedLanguageResolutionError as e:
        print(f"  ⚠️ SKIPPED {htid}: {e}")
        return pending("UNSUPPORTED_LANGUAGE", e.language)
    except UnsupportedStanzaLanguageError as e:
        print(f"  ⚠️ SKIPPED {htid}: {e}")
        return pending("UNSUPPORTED_STANZA_LANGUAGE", e.language)
    except StanzaPipelineInitializationError as e:
        print(f"  ⚠️ SKIPPED {htid}: {e}")
        return pending("STANZA_PIPELINE_UNAVAILABLE", str(e))
    except UnsupportedLanguageError as e:
        print(f"  ⚠️ SKIPPED {htid}: Unsupported Stopword Language ({e})")
        return pending("UNSUPPORTED_STOPWORD_LANGUAGE", str(e))
    except ImportError as e:
        print(f"  ⚠️ Skipping {htid} due to missing dependency: {e}")
        return pending("MISSING_DEPENDENCY", str(e))
    except Exception as e:
        print(f"  ❌ ERROR analyzing {htid}: {e}")
        detail = f"{type(e).__name__}: {e}"
        return pending("UNEXPECTED_ERROR", detail)

def validate_run_metadata(metadata_path, current_config):
    try:
        with open(metadata_path, "r", encoding="utf-8") as file:
            existing_config = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Invalid run metadata: {metadata_path}"
        ) from error

    if not isinstance(existing_config, dict):
        raise RuntimeError("run_metadata.json must contain a JSON object.")

    if existing_config != current_config:
        raise RuntimeError(
            "Configuration conflict: the output directory was created "
            "with different pipeline settings."
        )

def main():
    parser = argparse.ArgumentParser(description="Extract Ngrams from massive HathiTrust Pairtree storage on HPC clusters.")
    parser.add_argument("--pairtree", required=True, type=Path, help="Absolute path to the HathiTrust Pairtree root (e.g., /N/project/htrc/data/ht_text)")
    parser.add_argument("--id-list", required=True, type=Path, help="Absolute path to a text file containing HathiTrust Volume IDs (one per line)")
    parser.add_argument("--bibframe-root", type=Path, help="Optional root directory containing HTRC Bibframe JSON-LD metadata")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum volume-level frequency threshold for Filter 2 (default: 2)")
    parser.add_argument("--min-pmi", type=float, default=0.0, help="Minimum PMI threshold for Filter 3 (default: 0.0)")
    parser.add_argument("--max-exposure", type=float, default=0.15, help="Maximum token exposure ratio for Filter 4 (default: 0.15)")
    parser.add_argument("--max-zip-size-mb", type=float, default=150.0, help="Skip source ZIP files larger than this many MiB (default: 150)")
    parser.add_argument("--flat-output", action="store_true", help="Save TSV files in one flat directory (not recommended for more than 1 million volumes)")
    parser.add_argument("--filter1", action=argparse.BooleanOptionalAction, default=True, help="Enable Filter 1 (Standard Stopwords)")
    parser.add_argument("--filter2", action=argparse.BooleanOptionalAction, default=True, help="Enable Filter 2 (Min Frequency)")
    parser.add_argument("--filter3", action=argparse.BooleanOptionalAction, default=True, help="Enable Filter 3 (PMI)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to save volume TSVs and run_metadata.json")
    parser.add_argument("--audit-dir", type=Path, required=True, help="Directory to save audit files (e.g., dropped bigrams)")
    parser.add_argument("--metadata-dir", type=Path, required=True, help="Directory to save global run provenance metadata")
    parser.add_argument("--summary-dir", type=Path, help="Directory for task-sharded summary CSV files (default: <output-dir>/runs/<run-id>/results)")
    parser.add_argument("--pending-dir", type=Path, help="Directory for task-sharded pending-volume files (default: <output-dir>/runs/<run-id>/pending)")
    args = parser.parse_args()

    if args.min_freq < 1:
        parser.error("--min-freq must be at least 1")

    if args.max_zip_size_mb <= 0:
        parser.error("--max-zip-size-mb must be greater than zero")

    pairtree_root = args.pairtree
    htids_file = getattr(args, 'id_list')
    export_dir = args.output_dir
    audit_dir = args.audit_dir
    metadata_dir = args.metadata_dir
    summary_root = args.summary_dir
    pending_root = args.pending_dir
    ef_root = args.bibframe_root

    if not pairtree_root.exists() or not pairtree_root.is_dir():
        parser.error(f"Pairtree root not found or not a directory: {pairtree_root}")

    if not htids_file.is_file():
        parser.error(f"HTID list file not found: {htids_file}")

    if ef_root is not None and not ef_root.is_dir():
        parser.error(
            f"Bibframe metadata root not found or not a directory: {ef_root}"
        )

    print(f"Target Pairtree root: {pairtree_root}")
    print(f"Target ID list: {htids_file}")
    if ef_root is not None:
        print(f"Bibframe metadata root: {ef_root}")
    else:
        print("Bibframe metadata root: not configured; language detection will use text fallback")

    export_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    if summary_root is not None:
        summary_root.mkdir(parents=True, exist_ok=True)
    if pending_root is not None:
        pending_root.mkdir(parents=True, exist_ok=True)
    # Configuration Fingerprinting
    metadata_path = export_dir / "run_metadata.json"
    current_config = {
        "schema_version": "1.3",
        "pipeline_version": "2.0.0",
        "pending_schema_version": "1.0",
        "min_freq": args.min_freq,
        "min_pmi": args.min_pmi,
        "max_exposure": args.max_exposure,
        "max_zip_size_mb": args.max_zip_size_mb,
        "filter_1_enabled": args.filter1,
        "filter_2_enabled": args.filter2,
        "filter_3_enabled": args.filter3,
        "flat_output": args.flat_output,
        "pairtree_root": str(pairtree_root.resolve()),
        "bibframe_root": str(ef_root.resolve()) if ef_root is not None else None,
        "output_dir": str(export_dir.resolve()),
        "audit_dir": str(audit_dir.resolve()),
        "metadata_dir": str(metadata_dir.resolve()),
        "summary_dir": str(summary_root.resolve()) if summary_root is not None else None,
        "pending_dir": str(pending_root.resolve()) if pending_root is not None else None,
    }

    if metadata_path.exists():
        try:
            validate_run_metadata(metadata_path, current_config)
        except RuntimeError as e:
            print(f"\n❌ {e}")
            sys.exit(1)
    else:
        import uuid
        pid = os.getpid()
        uid = uuid.uuid4().hex[:8]
        tmp_meta = metadata_path.with_name(f"run_metadata.{pid}_{uid}.tmp")
        try:
            with open(tmp_meta, 'w', encoding='utf-8') as f:
                json.dump(current_config, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp_meta, metadata_path)
            except FileExistsError:
                try:
                    validate_run_metadata(metadata_path, current_config)
                except RuntimeError as e:
                    print(f"\n❌ {e}")
                    sys.exit(1)
        finally:
            if tmp_meta.exists():
                try:
                    tmp_meta.unlink()
                except OSError:
                    pass

    print(f"Export Directory configured at: {export_dir}")

    with open(htids_file, 'r', encoding='utf-8') as f:
        htids = []
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                htids.append(stripped)
        htids = list(dict.fromkeys(htids))

    if len(htids) == 0:
        parser.error("The HTID list contains no processable volume IDs")


    print(f"Loaded {len(htids)} Volume IDs to process.")
    print("=" * 70)

    # Read SLURM array environment variables
    # SLURM_ARRAY_TASK_ID = which task number this is (0, 1, 2...)
    # SLURM_ARRAY_TASK_COUNT = total number of tasks (e.g. 1000)
    task_id    = int(os.environ.get('SLURM_ARRAY_TASK_ID',    0))
    total_tasks = int(os.environ.get('SLURM_ARRAY_TASK_COUNT', 1))
    n_workers  = int(os.environ.get('SLURM_CPUS_PER_TASK',    1))

    # Stride slicing: each task picks every Nth volume from the master list
    my_htids = htids[task_id::total_tasks]
    print(f"[Task {task_id}/{total_tasks}] Processing {len(my_htids)} volumes using {n_workers} workers.")

    # Generate Run Provenance Manifest BEFORE worker execution so failed runs are captured
    slurm_array_id = os.environ.get("SLURM_ARRAY_JOB_ID")
    slurm_job_id = os.environ.get("SLURM_JOB_ID")
    if slurm_array_id:
        run_id = f"slurm_{slurm_array_id}"
    elif slurm_job_id:
        run_id = f"slurm_{slurm_job_id}"
    else:
        run_id = f"local_{time.strftime('%Y%m%d_%H%M%S')}"

    stanza_res_dir = Path(os.environ.get("STANZA_RESOURCES_DIR", Path.home() / "stanza_resources"))
    prov_data = generate_run_provenance(
        run_id, args.filter1, args.filter2, args.filter3,
        args.min_freq, args.min_pmi, stanza_res_dir
    )
    write_run_provenance_once(prov_data, metadata_dir)

    # Build argument tuples for unordered worker processing
    task_args = [(htid, pairtree_root, ef_root, export_dir, audit_dir, args.filter1, args.filter2, args.filter3, args.min_freq, args.min_pmi, args.max_exposure, args.max_zip_size_mb, args.flat_output) for htid in my_htids]

    # Task-Level Chunked Output
    task_logs_dir = export_dir / "runs" / run_id
    results_dir = summary_root / run_id if summary_root is not None else task_logs_dir / "results"
    pending_dir = pending_root / run_id if pending_root is not None else task_logs_dir / "pending"
    results_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    success_csv_path = results_dir / f"task_{task_id:04d}.csv"
    pending_tsv_path = pending_dir / f"task_{task_id:04d}.tsv"

    repair_truncated_log(success_csv_path)
    repair_truncated_log(pending_tsv_path)

    file_is_new = (
        not success_csv_path.exists()
        or success_csv_path.stat().st_size == 0
    )

    existing_result_htids = set()

    if not file_is_new:
        with open(
            success_csv_path,
            "r",
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.DictReader(file)

            if reader.fieldnames != RESULT_FIELDS:
                raise RuntimeError(
                    f"Unexpected results header in {success_csv_path}"
                )

            existing_result_htids = {
                row["htid"]
                for row in reader
                if row.get("htid")
            }

    existing_pending_records = set()
    pending_file_is_new = (
        not pending_tsv_path.exists()
        or pending_tsv_path.stat().st_size == 0
    )

    if not pending_file_is_new:
        with open(
            pending_tsv_path,
            "r",
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.DictReader(file, delimiter="\t")
            if reader.fieldnames != PENDING_FIELDS:
                raise RuntimeError(
                    f"Unexpected pending header in {pending_tsv_path}"
                )
            existing_pending_records = {
                (row["htid"], row["reason_code"])
                for row in reader
                if row.get("htid") and row.get("reason_code")
            }

    result_count = 0
    processed_count = 0
    checkpoint_count = 0
    pending_count = 0

    sum_tokens = 0
    sum_distinct_bigram_types = 0
    sum_bigram_occurrences = 0
    sum_singleton_percentage = 0
    sum_tsv_size_bytes = 0

    with open(
        success_csv_path, "a", newline="", encoding="utf-8"
    ) as success_f, open(
        pending_tsv_path, "a", newline="", encoding="utf-8"
    ) as pending_f:
        success_writer = csv.DictWriter(
            success_f,
            fieldnames=RESULT_FIELDS,
            extrasaction="raise",
        )
        if file_is_new:
            success_writer.writeheader()

        pending_writer = csv.DictWriter(
            pending_f,
            fieldnames=PENDING_FIELDS,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        if pending_file_is_new:
            pending_writer.writeheader()

        # Process this task's volumes using all available CPUs
        with Pool(processes=n_workers) as pool:
            for r in pool.imap_unordered(process_one, task_args):
                if r is None:
                    continue
                if r['type'] == 'success':
                    data = r['data']
                    data['run_id'] = run_id
                    data.setdefault("record_status", "processed")

                    htid = data["htid"]

                    if htid in existing_result_htids:
                        print(f"  Metadata already recorded for {htid}; skipping duplicate row.")
                        continue

                    success_writer.writerow({
                        field: data.get(field, "")
                        for field in RESULT_FIELDS
                    })
                    success_f.flush()


                    existing_result_htids.add(htid)
                    result_count += 1

                    if data["record_status"] == "checkpointed_existing":
                        checkpoint_count += 1
                    else:
                        processed_count += 1

                        sum_tokens += data["total_tokens"]
                        sum_distinct_bigram_types += data["distinct_bigram_types"]
                        sum_bigram_occurrences += data["total_bigram_occurrences"]
                        sum_singleton_percentage += data["singleton_percentage"]
                        sum_tsv_size_bytes += data["tsv_size_bytes"]

                elif r['type'] == 'pending':
                    safe_htid = sanitize_log_field(r["htid"])
                    safe_reason_code = sanitize_log_field(r["reason_code"])
                    safe_detail = sanitize_log_field(
                        r.get("detail", ""),
                        max_length=2000,
                    )

                    pending_record = (safe_htid, safe_reason_code)

                    if pending_record in existing_pending_records:
                        print(
                            f"  Pending metadata already recorded for "
                            f"{r['htid']}; skipping duplicate row."
                        )
                        continue

                    pending_writer.writerow({
                        "htid": safe_htid,
                        "reason_code": safe_reason_code,
                        "detail": safe_detail,
                    })
                    pending_f.flush()


                    existing_pending_records.add(pending_record)
                    pending_count += 1

    if result_count == 0 and pending_count == 0:
        print("\nNo new task metadata records were written.")
        return

    print("\n" + "=" * 70)
    print(f"Task {task_id} complete.")
    print(f"  Newly processed volumes: {processed_count}")
    print(f"  Existing checkpointed volumes: {checkpoint_count}")
    print(f"  Pending or failed volumes: {pending_count}")

    if result_count > 0:
        print(f"  Result metadata: {success_csv_path}")

    if pending_count > 0:
        print(f"  Pending log: {pending_tsv_path}")

    if processed_count > 0:
        avg_tokens = sum_tokens / processed_count
        avg_bigram_singletons = (
            sum_singleton_percentage / processed_count
        )
        total_tsv_size_mb = sum_tsv_size_bytes / (1024 * 1024)

        print(f"\nSUMMARY ACROSS {processed_count} NEWLY PROCESSED VOLUME(S)")
        print(f"  Total tokens:                         {sum_tokens:,}")
        print(f"  Average tokens per volume:            {avg_tokens:,.0f}")
        print(f"  Sum of volume-level bigram types:     {sum_distinct_bigram_types:,}")
        print(f"  Total bigram occurrences:             {sum_bigram_occurrences:,}")
        print(f"  Mean volume singleton percentage:     {avg_bigram_singletons:.1f}%")
        print(
            "  Total actual TSV size:                "
            f"{sum_tsv_size_bytes:,} bytes ({total_tsv_size_mb:.3f} MiB)"
        )

    print("=" * 70)

if __name__ == "__main__":
    main()
