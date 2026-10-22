import argparse
import csv
import os
import time
import itertools
from contextlib import ExitStack
from pathlib import Path
from collections import Counter
from multiprocessing import Pool

from src.engine_io import (
    CorruptZipError,
    MetadataAccessError,
    MetadataPermissionError,
    VolumeReadStats,
    ZipNotFoundError,
    ZipPermissionError,
    ZipReadError,
    get_zip_size_mb,
    htid_to_tsv_path,
    htid_to_zip_path,
    stream_volume_from_zip,
)
from src.engine_nlp import (
    LowConfidenceLanguageError,
    STANZA_PAGE_BATCH_SIZE,
    StanzaPipelineInitializationError,
    UnsupportedLanguageResolutionError,
    UnsupportedStanzaLanguageError,
    canonicalize_language,
    get_profile,
    is_supported_language,
    resolve_language,
    resolve_language_from_metadata,
    tokenize_page_batch,
)
from src.provenance import generate_run_provenance, write_run_provenance_once
from src.engine_export import atomic_write_json, atomic_write_tsv, is_complete_tsv
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


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_input_records(path: Path):
    """Load one-column HTID lists or two-column HTID/language shards."""
    records = []
    seen_records = {}

    with open(path, "r", encoding="utf-8", newline="") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            fields = stripped.split("\t")
            htid = fields[0].strip()
            language = fields[1].strip().lower() if len(fields) > 1 else ""

            if htid.lower() == "htid":
                continue
            if not htid:
                continue

            zip_size_bytes = None
            if len(fields) > 2 and fields[2].strip():
                try:
                    zip_size_bytes = int(fields[2].strip())
                except ValueError as error:
                    raise RuntimeError(
                        f"Invalid ZIP size for {htid} on line {line_number} "
                        f"of {path}: {fields[2]!r}"
                    ) from error

            previous_record = seen_records.get(htid)
            if previous_record is not None:
                previous_language, _ = previous_record
                if language and previous_language and language != previous_language:
                    raise RuntimeError(
                        f"Conflicting languages for {htid} in {path}: "
                        f"'{previous_language}' and '{language}'."
                    )
                continue

            seen_records[htid] = (language, zip_size_bytes)
            records.append((htid, language or None, zip_size_bytes))

    return records


RESULT_FIELDS = [
    "volume_name",
    "zip_path",
    "language",
    "output_file",
    "total_pages",
    "source_text_pages",
    "failed_pages",
    "decoding_warnings",
    "total_tokens",
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

def analyze_volume(htid: str, zip_path: Path, output_dir: Path,
                   max_zip_size_mb: float = 150.0, flat_output: bool = False,
                   ef_root: Path = None, collect_metrics: bool = False,
                   language_hint: str = None, check_existing: bool = True,
                   zip_size_bytes_hint: int = None, gzip_level: int = 1):

    volume_started_at = time.perf_counter()
    performance = Counter()

    if flat_output:
        safe_name = htid.replace('/', '=').replace(':', '+')
        tsv_path = output_dir / f"{safe_name}.tsv.gz"
    else:
        # Pairtree-mirrored output structure
        tsv_path = htid_to_tsv_path(htid, output_dir)
    volume_name = tsv_path.name.removesuffix(".tsv.gz")

    # Checkpointing: skip if this volume was already processed in a previous run
    checkpoint_started_at = time.perf_counter()
    if check_existing and is_complete_tsv(tsv_path):
        performance["checkpoint_seconds"] += (
            time.perf_counter() - checkpoint_started_at
        )
        performance["volume_seconds"] += time.perf_counter() - volume_started_at
        return {
            "volume_name": volume_name,
            "output_file": str(tsv_path),
            "record_status": "checkpointed_existing",
            "_performance": dict(performance),
        }
    performance["checkpoint_seconds"] += (
        time.perf_counter() - checkpoint_started_at
    )

    manifest_language = None
    if language_hint:
        manifest_language = canonicalize_language(language_hint)
        if not is_supported_language(manifest_language):
            raise UnsupportedLanguageResolutionError(
                language_hint, "input manifest"
            )

    # Size guard: skip volumes whose ZIP exceeds the configured threshold.
    zip_stat_started_at = time.perf_counter()
    if zip_size_bytes_hint is None:
        zip_size_mb = get_zip_size_mb(zip_path)
    else:
        zip_size_mb = zip_size_bytes_hint / (1024 * 1024)
    performance["zip_stat_seconds"] += time.perf_counter() - zip_stat_started_at
    if zip_size_mb > max_zip_size_mb:
        raise PendingVolumeError(
            "ZIP_TOO_LARGE",
            f"Compressed ZIP is {zip_size_mb:.1f} MiB; limit is {max_zip_size_mb:.1f} MiB.",
        )

    language_started_at = time.perf_counter()
    if manifest_language:
        lang = manifest_language
    else:
        lang = resolve_language_from_metadata(htid, ef_root=ef_root)
    if lang is None:
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
        lang = resolve_language(htid, sample_pages)
    performance["language_seconds"] += time.perf_counter() - language_started_at

    profile = get_profile(lang)

    bigram_counts = Counter()
    total_tokens = 0
    read_stats = VolumeReadStats()

    def count_tokenized_pages(page_tokens):
        nonlocal total_tokens
        for words in page_tokens:
            if not words:
                continue
            total_tokens += len(words)
            bigram_counts.update(
                zip(words, itertools.islice(words, 1, None))
            )

    page_batch = []
    for page in stream_volume_from_zip(zip_path, stats=read_stats):
        page_batch.append(page)
        if len(page_batch) == STANZA_PAGE_BATCH_SIZE:
            stanza_started_at = time.perf_counter()
            count_tokenized_pages(
                tokenize_page_batch(page_batch, profile, lang)
            )
            performance["stanza_seconds"] += (
                time.perf_counter() - stanza_started_at
            )
            page_batch.clear()

    if page_batch:
        stanza_started_at = time.perf_counter()
        count_tokenized_pages(
            tokenize_page_batch(page_batch, profile, lang)
        )
        performance["stanza_seconds"] += (
            time.perf_counter() - stanza_started_at
        )

    performance["zip_io_seconds"] += read_stats.io_seconds

    if total_tokens == 0:
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

    # Export securely formatted filename with atomic writes
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["w1", "w2", "freq"]
    rows = ([w1, w2, count] for (w1, w2), count in bigram_counts.items())

    performance.update(
        atomic_write_tsv(tsv_path, headers, rows, gzip_level=gzip_level)
    )
    performance["volume_seconds"] += time.perf_counter() - volume_started_at
    result = {
        'volume_name':          volume_name,
        'zip_path':             str(zip_path),
        'language':             lang,
        'output_file':          str(tsv_path),
        '_performance':         dict(performance),
    }

    if not collect_metrics:
        return result

    exported_counts = list(bigram_counts.values())

    distinct_bigram_types = len(exported_counts)
    total_bigram_occurrences = sum(exported_counts)
    singleton_percentage = (
        100.0 * sum(count == 1 for count in exported_counts)
        / distinct_bigram_types
        if distinct_bigram_types
        else 0.0
    )
    tsv_size_bytes = tsv_path.stat().st_size

    result.update({
        'total_pages':          read_stats.yielded_pages,
        'source_text_pages':    read_stats.total_page_files,
        'failed_pages':         read_stats.failed_pages,
        'decoding_warnings':    read_stats.decoding_warnings,
        'total_tokens':         total_tokens,
        'distinct_bigram_types': distinct_bigram_types,
        'total_bigram_occurrences': total_bigram_occurrences,
        'singleton_percentage': round(singleton_percentage, 1),
        'tsv_size_bytes':       tsv_size_bytes,
        'tsv_size_mb':          round(tsv_size_bytes / (1024 * 1024), 3),
    })
    return result

# -------------------------------------------------------------------------------------------
# Main (HPC Entrypoint)
# ---------------------------------------------------------------------------

def process_one(args_tuple):
    """Module-level wrapper to avoid multiprocessing PicklingError on local functions."""
    (
        htid, language_hint, zip_size_bytes_hint, pairtree_root, ef_root, export_dir,
        max_zip_size_mb, flat_output, collect_metrics, check_existing, gzip_level,
    ) = args_tuple
    zip_path = htid_to_zip_path(htid, pairtree_root)
    worker_started_at = time.perf_counter()

    def pending(reason_code, detail=""):
        return {
            'type': 'pending',
            'htid': htid,
            'reason_code': reason_code,
            'detail': detail,
            '_performance': {
                'volume_seconds': time.perf_counter() - worker_started_at,
            },
        }

    try:
        result = analyze_volume(htid, zip_path, export_dir,
                                max_zip_size_mb=max_zip_size_mb,
                                flat_output=flat_output,
                                ef_root=ef_root,
                                collect_metrics=collect_metrics,
                                language_hint=language_hint,
                                check_existing=check_existing,
                                zip_size_bytes_hint=zip_size_bytes_hint,
                                gzip_level=gzip_level)
        result['htid'] = htid
        return {'type': 'success', 'data': result}
    except PendingVolumeError as error:
        return pending(error.reason_code, error.detail)
    except CorruptZipError as error:
        return pending("ZIP_CORRUPT", error.detail)
    except ZipNotFoundError as error:
        return pending("ZIP_NOT_FOUND", str(error.zip_path))
    except ZipPermissionError as error:
        return pending("ZIP_PERMISSION_DENIED", error.detail)
    except ZipReadError as error:
        return pending("ZIP_READ_ERROR", error.detail)
    except MetadataPermissionError as error:
        return pending("METADATA_PERMISSION_DENIED", error.detail)
    except MetadataAccessError as error:
        return pending("METADATA_ACCESS_ERROR", error.detail)
    except LowConfidenceLanguageError as e:
        return pending("LOW_LANGUAGE_CONFIDENCE", str(e))
    except UnsupportedLanguageResolutionError as e:
        return pending("UNSUPPORTED_LANGUAGE", e.language)
    except UnsupportedStanzaLanguageError as e:
        return pending("UNSUPPORTED_STANZA_LANGUAGE", e.language)
    except StanzaPipelineInitializationError as e:
        return pending("STANZA_PIPELINE_UNAVAILABLE", str(e))
    except ImportError as e:
        return pending("MISSING_DEPENDENCY", str(e))
    except Exception as e:
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
    parser.add_argument("--max-zip-size-mb", type=float, default=150.0, help="Skip source ZIP files larger than this many MiB (default: 150)")
    parser.add_argument("--flat-output", action="store_true", help="Save TSV files in one flat directory (not recommended for more than 1 million volumes)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to save volume TSVs and run_metadata.json")
    parser.add_argument("--metadata-dir", type=Path, required=True, help="Directory to save global run provenance metadata")
    parser.add_argument("--summary-dir", type=Path, help="Optional directory for task-sharded summary CSV files. If omitted, summary CSV files are not written.")
    parser.add_argument("--pending-dir", type=Path, help="Directory for task-sharded pending-volume files (default: <output-dir>/task_logs/<run-id>/pending)")
    parser.add_argument("--input-is-task-shard", action="store_true", help="Process the entire input file because it was assigned specifically to this SLURM task")
    parser.add_argument("--skip-checkpoints", action="store_true", help="Skip existing-output checks for a guaranteed-new output directory")
    parser.add_argument("--gzip-level", type=int, default=1, help="gzip compression level for .tsv.gz outputs (0-9; default: 1)")
    parser.add_argument("--status-dir", type=Path, help="Directory for task completion records used by campaign validation")
    args = parser.parse_args()

    if args.max_zip_size_mb <= 0:
        parser.error("--max-zip-size-mb must be greater than zero")
    if not 0 <= args.gzip_level <= 9:
        parser.error("--gzip-level must be between 0 and 9")

    pairtree_root = args.pairtree
    htids_file = getattr(args, 'id_list')
    export_dir = args.output_dir
    metadata_dir = args.metadata_dir
    summary_root = args.summary_dir
    pending_root = args.pending_dir
    status_root = args.status_dir
    ef_root = args.bibframe_root

    if not pairtree_root.exists() or not pairtree_root.is_dir():
        parser.error(f"Pairtree root not found or not a directory: {pairtree_root}")

    if not htids_file.is_file():
        parser.error(f"HTID list file not found: {htids_file}")

    if ef_root is not None and not ef_root.is_dir():
        parser.error(
            f"Bibframe metadata root not found or not a directory: {ef_root}"
        )

    export_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    if summary_root is not None:
        summary_root.mkdir(parents=True, exist_ok=True)
    if pending_root is not None:
        pending_root.mkdir(parents=True, exist_ok=True)
    if status_root is not None:
        status_root.mkdir(parents=True, exist_ok=True)
    # Configuration Fingerprinting
    metadata_path = export_dir / "run_metadata.json"
    current_config = {
        "schema_version": "2.0",
        "pipeline_version": "3.2.0",
        "export_policy": "unfiltered",
        "output_format": "tsv.gz",
        "gzip_level": args.gzip_level,
        "pending_schema_version": "1.0",
        "stanza_page_batch_size": STANZA_PAGE_BATCH_SIZE,
        "max_zip_size_mb": args.max_zip_size_mb,
        "flat_output": args.flat_output,
        "pairtree_root": str(pairtree_root.resolve()),
        "bibframe_root": str(ef_root.resolve()) if ef_root is not None else None,
        "output_dir": str(export_dir.resolve()),
        "metadata_dir": str(metadata_dir.resolve()),
        "summary_dir": str(summary_root.resolve()) if summary_root is not None else None,
        "pending_dir": str(pending_root.resolve()) if pending_root is not None else None,
        "status_dir": str(status_root.resolve()) if status_root is not None else None,
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

    input_records = load_input_records(htids_file)

    if len(input_records) == 0:
        parser.error("The HTID list contains no processable volume IDs")

    if args.input_is_task_shard:
        shard_languages = {
            canonicalize_language(language)
            for _, language, _ in input_records
            if language
        }
        supported_shard_languages = {
            language
            for language in shard_languages
            if is_supported_language(language)
        }
        if (
            len(supported_shard_languages) > 1
            or (
                supported_shard_languages
                and supported_shard_languages != shard_languages
            )
        ):
            parser.error(
                "Supported-language task shards must be language-homogeneous; found: "
                + ", ".join(sorted(shard_languages))
            )

    # Read SLURM array environment variables
    # SLURM_ARRAY_TASK_ID = which task number this is (0, 1, 2...)
    # SLURM_ARRAY_TASK_COUNT = total number of tasks (e.g. 1000)
    task_id    = int(os.environ.get('SLURM_ARRAY_TASK_ID',    0))
    total_tasks = int(os.environ.get('SLURM_ARRAY_TASK_COUNT', 1))
    n_workers  = int(os.environ.get('SLURM_CPUS_PER_TASK',    1))

    if args.input_is_task_shard:
        task_records = input_records
    else:
        # Compatibility mode for existing unsharded HTID lists.
        task_records = input_records[task_id::total_tasks]

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
    provenance_run_id = os.environ.get("PROVENANCE_RUN_ID", run_id)
    write_provenance = os.environ.get("WRITE_PROVENANCE", "1") == "1"
    if task_id == 0 and write_provenance:
        prov_data = generate_run_provenance(
            provenance_run_id,
            stanza_res_dir,
            output_format="tsv.gz",
            gzip_level=args.gzip_level,
        )
        write_run_provenance_once(prov_data, metadata_dir)

    collect_metrics = summary_root is not None
    task_args = [
        (
            htid, language_hint, zip_size_bytes_hint,
            pairtree_root, ef_root, export_dir,
            args.max_zip_size_mb, args.flat_output, collect_metrics,
            not args.skip_checkpoints, args.gzip_level,
        )
        for htid, language_hint, zip_size_bytes_hint in task_records
    ]

    # Task-level pending records and optional summary output
    task_logs_dir = export_dir / "task_logs" / run_id
    results_dir = summary_root / run_id if summary_root is not None else None
    pending_dir = pending_root / run_id if pending_root is not None else task_logs_dir / "pending"
    if results_dir is not None:
        results_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    success_csv_path = (
        results_dir / f"task_{task_id:04d}.csv"
        if results_dir is not None
        else None
    )
    pending_tsv_path = pending_dir / f"task_{task_id:04d}.tsv"

    if success_csv_path is not None:
        repair_truncated_log(success_csv_path)
    repair_truncated_log(pending_tsv_path)

    file_is_new = (
        success_csv_path is not None
        and (
            not success_csv_path.exists()
            or success_csv_path.stat().st_size == 0
        )
    )

    existing_result_htids = set()

    if success_csv_path is not None and not file_is_new:
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
    attempted_count = 0
    performance_totals = Counter()
    progress_interval = 1000
    task_started_at = time.monotonic()

    print(
        f"Task {task_id}/{total_tasks} started: {len(task_records)} HTIDs, "
        f"{min(n_workers, len(task_records))} workers."
    )

    with ExitStack() as stack:
        success_f = None
        success_writer = None
        if success_csv_path is not None:
            success_f = stack.enter_context(
                open(success_csv_path, "a", newline="", encoding="utf-8")
            )
            success_writer = csv.DictWriter(
                success_f,
                fieldnames=RESULT_FIELDS,
                extrasaction="raise",
            )
            if file_is_new:
                success_writer.writeheader()

        pending_f = stack.enter_context(
            open(pending_tsv_path, "a", newline="", encoding="utf-8")
        )
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
        worker_count = min(n_workers, len(task_records))
        with Pool(processes=worker_count) as pool:
            for r in pool.imap_unordered(process_one, task_args):
                attempted_count += 1
                if r is None:
                    raise RuntimeError("A worker returned no result.")

                performance_totals.update(r.get("_performance", {}))

                if r['type'] == 'success':
                    data = r['data']
                    performance_totals.update(data.pop("_performance", {}))
                    data['run_id'] = run_id
                    data.setdefault("record_status", "processed")

                    htid = data["htid"]

                    if (
                        success_writer is not None
                        and htid in existing_result_htids
                    ):
                        pass
                    elif success_writer is not None:
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

                elif r['type'] == 'pending':
                    safe_htid = sanitize_log_field(r["htid"])
                    safe_reason_code = sanitize_log_field(r["reason_code"])
                    safe_detail = sanitize_log_field(
                        r.get("detail", ""),
                        max_length=2000,
                    )

                    pending_record = (safe_htid, safe_reason_code)

                    if pending_record not in existing_pending_records:
                        pending_writer.writerow({
                            "htid": safe_htid,
                            "reason_code": safe_reason_code,
                            "detail": safe_detail,
                        })
                        pending_f.flush()
                        existing_pending_records.add(pending_record)
                    pending_count += 1

                else:
                    raise RuntimeError(
                        f"Worker returned unknown result type: {r.get('type')}"
                    )

                if attempted_count % progress_interval == 0:
                    elapsed = format_elapsed(time.monotonic() - task_started_at)
                    print(
                        f"Task {task_id} progress: {attempted_count}/"
                        f"{len(task_records)} attempted, {result_count} successful, "
                        f"{pending_count} pending, elapsed {elapsed}."
                    )

    elapsed = format_elapsed(time.monotonic() - task_started_at)
    elapsed_seconds = time.monotonic() - task_started_at

    if status_root is not None:
        status_path = status_root / run_id / f"task_{task_id:04d}.json"
        atomic_write_json(
            status_path,
            {
                "run_id": run_id,
                "task_id": task_id,
                "total_tasks": total_tasks,
                "input_records": len(task_records),
                "attempted": attempted_count,
                "successful": result_count,
                "processed_new": processed_count,
                "checkpointed": checkpoint_count,
                "pending": pending_count,
                "elapsed_seconds": round(elapsed_seconds, 3),
            },
        )

    print(
        f"Task {task_id} completed: {attempted_count} attempted, "
        f"{result_count} successful ({processed_count} new, "
        f"{checkpoint_count} checkpointed), {pending_count} pending, "
        f"elapsed {elapsed}."
    )
    print(
        "Task performance totals (worker-seconds): "
        f"volume={performance_totals['volume_seconds']:.3f}, "
        f"checkpoint={performance_totals['checkpoint_seconds']:.3f}, "
        f"zip_stat={performance_totals['zip_stat_seconds']:.3f}, "
        f"language={performance_totals['language_seconds']:.3f}, "
        f"zip_io={performance_totals['zip_io_seconds']:.3f}, "
        f"stanza={performance_totals['stanza_seconds']:.3f}, "
        f"export={performance_totals['export_seconds']:.3f}, "
        f"fsync={performance_totals['export_fsync_seconds']:.3f}."
    )

if __name__ == "__main__":
    main()
