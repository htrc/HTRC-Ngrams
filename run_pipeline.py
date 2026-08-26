import argparse
import csv
import os
import time
import itertools
from pathlib import Path
from collections import Counter
from multiprocessing import Pool

from src.engine_io import htid_to_zip_path, htid_to_tsv_path, get_zip_size_mb, stream_volume_from_zip
from src.engine_nlp import (
    LowConfidenceLanguageError,
    StanzaPipelineInitializationError,
    UnsupportedStanzaLanguageError,
    generate_ngrams_with_counts,
    get_profile,
    resolve_language,
    tokenize_page,
)
from src.engine_filters import select_top_standard_stopwords, filter_1_standard_stopword_bigrams, filter2_min_frequency, filter3_pmi, filter4_exposure_cap, UnsupportedLanguageError, get_stopword_metadata
from src.metrics import estimate_json_size, singleton_rate
from src.provenance import generate_run_provenance, write_run_provenance_once
from src.engine_export import is_complete_tsv, atomic_write_tsv
import json
import sys

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
    "total_tokens",
    "unique_unigrams",
    "unique_bigrams",
    "total_bigrams",
    "unigram_mb",
    "bigram_mb",
    "bigram_growth",
    "bigram_singleton_pct",
    "htid",
    "run_id",
    "record_status",
]

# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def analyze_volume(htid: str, zip_path: Path, output_dir: Path, audit_dir: Path,
                   enable_f1: bool, enable_f2: bool, enable_f3: bool,
                   min_freq: int = 2, min_pmi: float = 0.0, max_exposure: float = 0.15,
                   flat_output: bool = False, ef_root: Path = None):
    print(f"\nAnalyzing: {htid}")

    if flat_output:
        safe_name = htid.replace('/', '=').replace(':', '+')
        tsv_path = output_dir / f"{safe_name}.tsv"
    else:
        # Pairtree-mirrored structure (Blocker 6 fix)
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
    MAX_ZIP_SIZE_MB = 150
    zip_size_mb = get_zip_size_mb(zip_path)
    if zip_size_mb > MAX_ZIP_SIZE_MB:
        print(f"  ⚠️  OVERSIZED ({zip_size_mb:.1f} MB): {volume_name}")
        return f"OVERSIZED_{zip_size_mb:.1f}MB"

    # --- LANGUAGE DETECTION ---
    # Fetch 61 pages so resolve_language() can sample interior pages 40-60
    sample_pages = list(itertools.islice(stream_volume_from_zip(zip_path), 61))
    lang = resolve_language(htid, sample_pages, ef_root=ef_root)
    profile = get_profile(lang)

    # --- STREAMING PROCESSING PASS ---
    unigram_counts = Counter()
    bigram_counts  = Counter()
    total_tokens   = 0
    page_count     = 0

    for page in stream_volume_from_zip(zip_path):
        page_count += 1
        words = tokenize_page(page, profile, lang)
        if not words:
            continue
        total_tokens += len(words)
        unigram_counts.update(words)
        bigram_counts.update(generate_ngrams_with_counts(words, 2))

    if total_tokens == 0:
        print(f"  ⚠️  WARNING: No words found in {volume_name}")
        return "NO_TOKENS_FOUND"

    print(f"  Total pages:  {page_count:,}")
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
        print(f"  Filter 1 (Standard Stopwords): Dropped {original_bigram_count - len(bigram_counts):,} useless bigrams.")

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
        print(f"  Filter 3 (PMI Polish >= {min_pmi}): Evaluated {f2_survivor_count:,} bigrams; dropped {pmi_dropped:,} low-scoring artifacts; retained {len(scored_results):,} bigrams.")
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

    unigram_mb  = estimate_json_size(unigram_counts)

    if has_pmi:
        final_bigram_counts = Counter(
            {bigram: count for bigram, count, _ in final_results}
        )
    else:
        final_bigram_counts = Counter(
            {bigram: count for bigram, count in final_results}
        )

    bigram_mb   = estimate_json_size(final_bigram_counts)

    bigram_growth   = bigram_mb   / unigram_mb if unigram_mb > 0 else 0
    bigram_singleton_pct   = singleton_rate(final_bigram_counts)

    print(f"  Bigrams:  {bigram_mb:.2f} MB ({bigram_growth:.2f}x, {bigram_singleton_pct:.1f}% singletons)")

    if has_pmi:
        total_bigrams = sum(b_count for _, b_count, _ in final_results)
    else:
        total_bigrams = sum(b_count for _, b_count in final_results)

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
        'total_tokens':         total_tokens,
        'unique_unigrams':      len(unigram_counts),
        'unique_bigrams':       len(final_results),
        'total_bigrams':        total_bigrams,
        'unigram_mb':           round(unigram_mb,  3),
        'bigram_mb':            round(bigram_mb,   3),
        'bigram_growth':        round(bigram_growth,   2),
        'bigram_singleton_pct': round(bigram_singleton_pct, 1),
    }

# -------------------------------------------------------------------------------------------
# Main (HPC Entrypoint)
# ---------------------------------------------------------------------------

def process_one(args_tuple):
    """Module-level wrapper to avoid multiprocessing PicklingError on local functions."""
    htid, pairtree_root, ef_root, export_dir, audit_dir, enable_f1, enable_f2, enable_f3, min_freq, min_pmi, max_exposure, flat_output = args_tuple
    zip_path = htid_to_zip_path(htid, pairtree_root)

    def pending(reason):
        return {'type': 'pending', 'htid': htid, 'reason': reason}

    if not zip_path.exists():
        print(f"  ❌ ZIP not found: {htid}")
        return pending("ZIP_NOT_FOUND")

    try:
        result = analyze_volume(htid, zip_path, export_dir, audit_dir,
                                enable_f1=enable_f1,
                                enable_f2=enable_f2,
                                enable_f3=enable_f3,
                                min_freq=min_freq,
                                min_pmi=min_pmi,
                                max_exposure=max_exposure,
                                flat_output=flat_output,
                                ef_root=ef_root)
        if isinstance(result, str):
            # analyze_volume returned a reason string instead of a result dict
            return pending(result)
        else:
            result['htid'] = htid
            return {'type': 'success', 'data': result}
    except LowConfidenceLanguageError as e:
        print(f"  🔍 SKIPPED {htid}: {e}")
        return pending("LOW_LANGUAGE_CONFIDENCE")
    except UnsupportedStanzaLanguageError as e:
        print(f"  ⚠️ SKIPPED {htid}: {e}")
        return pending(f"UNSUPPORTED_STANZA_LANGUAGE_{e.language}")
    except StanzaPipelineInitializationError as e:
        print(f"  ⚠️ SKIPPED {htid}: {e}")
        return pending(f"STANZA_PIPELINE_UNAVAILABLE_{e.language}")
    except UnsupportedLanguageError as e:
        print(f"  ⚠️ SKIPPED {htid}: Unsupported Stopword Language ({e})")
        return pending(f"UNSUPPORTED_LANGUAGE_{e}")
    except ImportError as e:
        print(f"  ⚠️ Skipping {htid} due to missing dependency: {e}")
        return pending(f"MISSING_DEPENDENCY_{e}")
    except Exception as e:
        print(f"  ❌ ERROR analyzing {htid}: {e}")
        return pending(f"UNKNOWN_ERROR_{e}")

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
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum volume-level frequency threshold for Filter 2 (default: 2)")
    parser.add_argument("--min-pmi", type=float, default=0.0, help="Minimum PMI threshold for Filter 3 (default: 0.0)")
    parser.add_argument("--max-exposure", type=float, default=0.15, help="Maximum token exposure ratio for Filter 4 (default: 0.15)")
    parser.add_argument("--flat-output", action="store_true", help="Save TSV files in a single flat folder for testing (WARNING: Do not use for >1M volumes)")
    parser.add_argument("--filter1", action=argparse.BooleanOptionalAction, default=True, help="Enable Filter 1 (Standard Stopwords)")
    parser.add_argument("--filter2", action=argparse.BooleanOptionalAction, default=False, help="Enable Filter 2 (Min Frequency)")
    parser.add_argument("--filter3", action=argparse.BooleanOptionalAction, default=True, help="Enable Filter 3 (PMI)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to save volume TSVs and task logs")
    parser.add_argument("--audit-dir", type=Path, required=True, help="Directory to save audit files (e.g., dropped bigrams)")
    parser.add_argument("--metadata-dir", type=Path, required=True, help="Directory to save global run provenance metadata")
    args = parser.parse_args()

    pairtree_root = args.pairtree
    htids_file = getattr(args, 'id_list')
    export_dir = args.output_dir
    audit_dir = args.audit_dir
    metadata_dir = args.metadata_dir

    if not pairtree_root.exists() or not pairtree_root.is_dir():
        print(f"❌ Pairtree root not found at: {pairtree_root}")
        return

    if not htids_file.exists():
        print(f"❌ HTIDs list file not found at: {htids_file}")
        return

    print(f"Target Pairtree root: {pairtree_root}")
    print(f"Target ID list: {htids_file}")

    ef_root = Path("/N/project/htrc/extracted-features/data/bibframe-jsonld")

    export_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    # Configuration Fingerprinting
    metadata_path = export_dir / "run_metadata.json"
    current_config = {
        "schema_version": "1.0",
        "pipeline_version": "2.0.0",
        "min_freq": args.min_freq,
        "min_pmi": args.min_pmi,
        "max_exposure": args.max_exposure,
        "filter_1_enabled": args.filter1,
        "filter_2_enabled": args.filter2,
        "filter_3_enabled": args.filter3,
        "flat_output": args.flat_output,
        "audit_dir": str(audit_dir.resolve())
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
        htids = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        htids = list(dict.fromkeys(htids))

    if len(htids) == 0:
        print(f"❌ The ID list file is empty!")
        return


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

    # Build argument tuples for each volume to pass to pool.map
    task_args = [(htid, pairtree_root, ef_root, export_dir, audit_dir, args.filter1, args.filter2, args.filter3, args.min_freq, args.min_pmi, args.max_exposure, args.flat_output) for htid in my_htids]

    # Task-Level Chunked Output
    task_logs_dir = export_dir / "runs" / run_id
    results_dir = task_logs_dir / "results"
    pending_dir = task_logs_dir / "pending"
    results_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    success_csv_path = results_dir / f"task_{task_id:04d}.csv"
    pending_txt_path = pending_dir / f"task_{task_id:04d}.txt"

    repair_truncated_log(success_csv_path)
    repair_truncated_log(pending_txt_path)

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

    if pending_txt_path.exists():
        with open(pending_txt_path, "r", encoding="utf-8") as file:
            for line in file:
                htid, separator, reason = line.rstrip("\n").partition("\t")

                if separator:
                    existing_pending_records.add((htid, reason))

    result_count = 0
    processed_count = 0
    checkpoint_count = 0
    pending_count = 0

    sum_tokens = 0
    sum_bg_growth = 0
    sum_bg_s = 0

    with open(
        success_csv_path, "a", newline="", encoding="utf-8"
    ) as success_f, open(
        pending_txt_path, "a", encoding="utf-8"
    ) as pending_f:
        success_writer = csv.DictWriter(
            success_f,
            fieldnames=RESULT_FIELDS,
            extrasaction="raise",
        )
        if file_is_new:
            success_writer.writeheader()

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
                        sum_bg_growth += data["bigram_growth"]
                        sum_bg_s += data["bigram_singleton_pct"]

                elif r['type'] == 'pending':
                    safe_reason = str(r['reason'])
                    safe_reason = safe_reason.replace("\t", " ")
                    safe_reason = safe_reason.replace("\r", " ")
                    safe_reason = safe_reason.replace("\n", " ")

                    pending_record = (r["htid"], safe_reason)

                    if pending_record in existing_pending_records:
                        print(
                            f"  Pending metadata already recorded for "
                            f"{r['htid']}; skipping duplicate row."
                        )
                        continue

                    pending_f.write(f"{r['htid']}\t{safe_reason}\n")
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
        print(f"  Pending log: {pending_txt_path}")

    if processed_count > 0:
        avg_tokens = sum_tokens / processed_count
        avg_bigram_growth = sum_bg_growth / processed_count
        avg_bigram_singletons = sum_bg_s / processed_count

        print(f"\nSUMMARY ACROSS {processed_count} NEWLY PROCESSED VOLUME(S)")
        print(f"  Avg Tokens: {avg_tokens:,.0f}")
        print(
            "  Avg Storage Growth: "
            f"Bigrams ({avg_bigram_growth:.2f}x)"
        )
        print(
            "  Avg Singleton Rate: "
            f"Bigrams ({avg_bigram_singletons:.1f}%)"
        )

    print("=" * 70)

if __name__ == "__main__":
    main()
