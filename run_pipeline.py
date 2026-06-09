import argparse
import csv
import os
import time
import random
import itertools
from pathlib import Path
from collections import Counter
from multiprocessing import Pool

from src.engine_io import htid_to_zip_path, htid_to_tsv_path, get_zip_size_mb, stream_volume_from_zip
from src.engine_nlp import resolve_language, get_profile, tokenize_page, generate_ngrams_with_counts, LowConfidenceLanguageError
from src.engine_filters import filter1_top25, filter2_min_frequency, filter3_pmi, filter4_exposure_cap
from src.metrics import estimate_json_size, singleton_rate

# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def safe_append_result(result: dict, csv_path: Path):
    """
    Safely appends one result row to the shared CSV using a lockfile.
    Works on Lustre (BigRed200) where fcntl locks are unreliable.
    Each task holds the lock for < 1 millisecond — no meaningful delay.
    """
    lock_path = csv_path.with_suffix('.lock')
    # Acquire lock — spin until we can create the lockfile exclusively
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break   # we have the lock
        except FileExistsError:
            time.sleep(0.05 + random.random() * 0.1)   # wait briefly, retry
    try:
        file_is_new = not csv_path.exists() or csv_path.stat().st_size == 0
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=result.keys())
            if file_is_new:
                writer.writeheader()
            writer.writerow(result)
    finally:
        os.unlink(str(lock_path))   # always release lock

def write_pending(htid: str, reason: str, pending_path: Path):
    """
    Safely appends an unprocessed volume + reason to the shared pending file.
    Uses the same lockfile pattern as safe_append_result.
    """
    lock_path = pending_path.with_suffix('.lock')
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(0.05 + random.random() * 0.1)
    try:
        with open(pending_path, 'a', encoding='utf-8') as pf:
            pf.write(f"{htid}\t{reason}\n")
    finally:
        os.unlink(str(lock_path))

def analyze_volume(htid: str, zip_path: Path, output_dir: Path, min_freq: int = 2, min_pmi: float = 0.0, max_exposure: float = 0.15, flat_output: bool = False, ef_root: Path = None):
    print(f"\nAnalyzing: {htid}")
    
    if flat_output:
        safe_name = htid.replace('/', '=').replace(':', '+')
        tsv_path = output_dir / f"{safe_name}.tsv"
    else:
        # Pairtree-mirrored structure (Blocker 6 fix)
        tsv_path = htid_to_tsv_path(htid, output_dir)
    volume_name = tsv_path.stem
    
    # Checkpointing: skip if this volume was already processed in a previous run
    if tsv_path.exists():
        print(f"  ⏭️  SKIP (already done): {volume_name}")
        return None
        
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
        total_tokens        += len(words)
        unigram_counts.update(words)
        bigram_counts.update(generate_ngrams_with_counts(words, 2))

    if total_tokens == 0:
        print(f"  ⚠️  WARNING: No words found in {volume_name}")
        return "NO_TOKENS_FOUND"

    print(f"  Total pages:  {page_count:,}")
    print(f"  Total tokens: {total_tokens:,}")
    
    original_total_bigrams = sum(bigram_counts.values())

    # --- APPLY FILTER 1 ---
    original_bigram_count = len(bigram_counts)
    bigram_counts, top25_words = filter1_top25(bigram_counts, unigram_counts)
    print(f"  Filter 1 (Top 25 Stopwords): Dropped {original_bigram_count - len(bigram_counts):,} useless bigrams.")
    
    # --- APPLY FILTER 2 (DISABLED PER USER REQUEST) ---
    # bigram_counts, rem_dist, rem_occ = filter2_min_frequency(bigram_counts, min_freq=min_freq)
    # print(f"  Filter 2 (Min Freq >= {min_freq}): Dropped {rem_dist:,} distinct bigrams; retained {len(bigram_counts):,} distinct bigrams; removed {rem_occ:,} total occurrences.")
    
    # --- APPLY FILTER 3 ---
    f2_survivor_count = len(bigram_counts)
    scored_results, pmi_dropped = filter3_pmi(bigram_counts, unigram_counts, original_total_bigrams, total_tokens, min_pmi=min_pmi)
    print(f"  Filter 3 (PMI Polish >= {min_pmi}): Evaluated {f2_survivor_count:,} bigrams; dropped {pmi_dropped:,} low-scoring artifacts; retained {len(scored_results):,} bigrams.")
    
    # --- APPLY FILTER 4 (DISABLED PER USER REQUEST) ---
    final_results = scored_results

    # Export securely formatted filename
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write("w1\tw2\tfreq\tpmi\n")
        for bigram, b_count, pmi in final_results:
            w1, w2 = bigram.split(' ', 1)
            f.write(f"{w1}\t{w2}\t{b_count}\t{pmi}\n")
    print(f"  Exported TSV: {tsv_path.name} ({len(final_results):,} records)")

    unigram_mb  = estimate_json_size(unigram_counts)
    bigram_mb   = estimate_json_size(bigram_counts)

    bigram_growth   = bigram_mb   / unigram_mb if unigram_mb > 0 else 0
    bigram_singleton_pct   = singleton_rate(bigram_counts)

    print(f"  Bigrams:  {bigram_mb:.2f} MB ({bigram_growth:.2f}x, {bigram_singleton_pct:.1f}% singletons)")

    return {
        'volume_name':          volume_name,
        'zip_path':             str(zip_path),
        'total_pages':          page_count,
        'total_tokens':         total_tokens,
        'unique_unigrams':      len(unigram_counts),
        'unique_bigrams':       len(final_results),
        'unigram_mb':           round(unigram_mb,  3),
        'bigram_mb':            round(bigram_mb,   3),
        'bigram_growth':        round(bigram_growth,   2),
        'bigram_singleton_pct': round(bigram_singleton_pct, 1),
    }


# ---------------------------------------------------------------------------
# Main (HPC Entrypoint)
# ---------------------------------------------------------------------------

def process_one(args_tuple):
    """Module-level wrapper to avoid multiprocessing PicklingError on local functions."""
    htid, pairtree_root, ef_root, export_dir, min_freq, min_pmi, max_exposure, flat_output = args_tuple
    zip_path = htid_to_zip_path(htid, pairtree_root)
    pending_path = Path("/N/project/htrc/HTRC-Ngrams/pending_volumes.txt")
    
    if not zip_path.exists():
        print(f"  ❌ ZIP not found: {htid}")
        write_pending(htid, "ZIP_NOT_FOUND", pending_path)
        return None
        
    try:
        result = analyze_volume(htid, zip_path, export_dir, 
                                min_freq=min_freq, 
                                min_pmi=min_pmi, 
                                max_exposure=max_exposure,
                                flat_output=flat_output,
                                ef_root=ef_root)
        if result is None:
            # Checkpointing skip returns None, do not log as pending
            return None
        elif isinstance(result, str):
            # analyze_volume returned a reason string instead of a result dict
            write_pending(htid, result, pending_path)
            return None
        else:
            result['htid'] = htid
            return result
    except LowConfidenceLanguageError as e:
        print(f"  🔍 SKIPPED {htid}: {e}")
        write_pending(htid, "LOW_LANGUAGE_CONFIDENCE", pending_path)
        return None
    except ImportError as e:
        print(f"  ⚠️ Skipping {htid} due to missing dependency: {e}")
        write_pending(htid, f"MISSING_DEPENDENCY_{e}", pending_path)
        return None
    except Exception as e:
        print(f"  ❌ ERROR analyzing {htid}: {e}")
        write_pending(htid, f"UNKNOWN_ERROR_{e}", pending_path)
        return None

def main():
    parser = argparse.ArgumentParser(description="Extract Ngrams from massive HathiTrust Pairtree storage on HPC clusters.")
    parser.add_argument("--pairtree", required=True, type=Path, help="Absolute path to the HathiTrust Pairtree root (e.g., /N/project/htrc/data/ht_text)")
    parser.add_argument("--id-list", required=True, type=Path, help="Absolute path to a text file containing HathiTrust Volume IDs (one per line)")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum volume-level frequency threshold for Filter 2 (default: 2)")
    parser.add_argument("--min-pmi", type=float, default=0.0, help="Minimum PMI threshold for Filter 3 (default: 0.0)")
    parser.add_argument("--max-exposure", type=float, default=0.15, help="Maximum token exposure ratio for Filter 4 (default: 0.15)")
    parser.add_argument("--flat-output", action="store_true", help="Save TSV files in a single flat folder for testing (WARNING: Do not use for >1M volumes)")
    args = parser.parse_args()

    pairtree_root = args.pairtree
    htids_file = getattr(args, 'id_list')

    if not pairtree_root.exists() or not pairtree_root.is_dir():
        print(f"❌ Pairtree root not found at: {pairtree_root}")
        return

    if not htids_file.exists():
        print(f"❌ HTIDs list file not found at: {htids_file}")
        return

    print(f"Target Pairtree root: {pairtree_root}")
    print(f"Target ID list: {htids_file}")

    ef_root = Path("/N/project/htrc/extracted-features/data/bibframe-jsonld")

    # Hardcoded permanent storage path (Blocker 9 fix)
    if args.flat_output:
        export_dir = Path("/N/project/htrc/HTRC-Ngrams/exported_bigrams_flat")
    else:
        export_dir = Path("/N/project/htrc/HTRC-Ngrams/exported_bigrams")
    export_dir.mkdir(parents=True, exist_ok=True)
    print(f"Export Directory configured at: {export_dir}")

    with open(htids_file, 'r', encoding='utf-8') as f:
        htids = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
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

    # Build argument tuples for each volume to pass to pool.map
    task_args = [(htid, pairtree_root, ef_root, export_dir, args.min_freq, args.min_pmi, args.max_exposure, args.flat_output) for htid in my_htids]

    # Process this task's volumes using all available CPUs
    with Pool(processes=n_workers) as pool:
        results = pool.map(process_one, task_args)

    all_results = [r for r in results if r is not None]

    if len(all_results) == 0:
        print("\n❌ No volumes successfully analyzed by this task. Exiting.")
        return

    # All tasks append their rows to one unified file securely using a lockfile
    csv_path = Path("/N/project/htrc/HTRC-Ngrams/bigred_results.csv")
    for result in all_results:
        safe_append_result(result, csv_path)

    print("\n" + "=" * 70)
    print(f"✅ Fast-track HPC analysis complete! Results appended to: {csv_path.name}")

    n = len(all_results)
    avg_tokens          = sum(r['total_tokens']         for r in all_results) / n
    avg_bigram_growth   = sum(r['bigram_growth']        for r in all_results) / n
    avg_bigram_s        = sum(r['bigram_singleton_pct'] for r in all_results) / n

    print(f"\nSUMMARY ACROSS {n} VOLUME(S)")
    print(f"  Avg Tokens: {avg_tokens:,.0f}")
    print(f"  Avg Storage Growth (vs unigrams): Bigrams ({avg_bigram_growth:.2f}x)")
    print(f"  Avg Singleton Rate: Bigrams ({avg_bigram_s:.1f}%)")
    print("=" * 70)

if __name__ == "__main__":
    main()
