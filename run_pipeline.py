import argparse
import csv
from pathlib import Path
from collections import Counter

from src.engine_io import htid_to_zip_path, read_volume_from_zip
from src.engine_nlp import resolve_language, get_profile, tokenize_page, generate_ngrams_with_counts
from src.engine_filters import filter1_top25, filter2_min_frequency, filter3_pmi, filter4_exposure_cap
from src.metrics import estimate_json_size, singleton_rate

# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def analyze_volume(zip_path: Path, output_dir: Path, min_freq: int = 2, min_pmi: float = 0.0, max_exposure: float = 0.15):
    """Analyze a single volume zip and return a statistics dictionary."""
    volume_name = zip_path.stem
    print(f"\nAnalyzing: {volume_name}")
    
    try:
        pages = read_volume_from_zip(zip_path)
        page_count = len(pages)
    except Exception as e:
        print(f"  ❌ ERROR reading zip {zip_path.name}: {e}")
        return None
        
    lang = resolve_language(volume_name, pages)
    profile = get_profile(lang)

    unigram_counts = Counter()
    bigram_counts = Counter()
    total_tokens = 0
    
    for page in pages:
        words = tokenize_page(page, profile)
        if len(words) == 0:
            continue
            
        total_tokens += len(words)
        unigram_counts.update(words)
        bigram_counts.update(generate_ngrams_with_counts(words, 2))

    if total_tokens == 0:
        print(f"  ⚠️  WARNING: No words found in {volume_name}")
        return None

    print(f"  Total pages:  {page_count:,}")
    print(f"  Total tokens: {total_tokens:,}")
    
    original_total_bigrams = sum(bigram_counts.values())

    # --- APPLY FILTER 1 ---
    original_bigram_count = len(bigram_counts)
    bigram_counts, top25_words = filter1_top25(bigram_counts, unigram_counts)
    print(f"  Filter 1 (Top 25 Stopwords): Dropped {original_bigram_count - len(bigram_counts):,} useless bigrams.")
    
    # --- APPLY FILTER 2 ---
    bigram_counts, rem_dist, rem_occ = filter2_min_frequency(bigram_counts, min_freq=min_freq)
    print(f"  Filter 2 (Min Freq >= {min_freq}): Dropped {rem_dist:,} distinct bigrams; retained {len(bigram_counts):,} distinct bigrams; removed {rem_occ:,} total occurrences.")
    
    # --- APPLY FILTER 3 ---
    f2_survivor_count = len(bigram_counts)
    scored_results, pmi_dropped = filter3_pmi(bigram_counts, unigram_counts, original_total_bigrams, total_tokens, min_pmi=min_pmi)
    print(f"  Filter 3 (PMI Polish >= {min_pmi}): Evaluated {f2_survivor_count:,} bigrams; dropped {pmi_dropped:,} low-scoring artifacts; retained {len(scored_results):,} bigrams.")
    
    # --- APPLY FILTER 4 ---
    final_results, f4_stats = filter4_exposure_cap(scored_results, total_tokens, max_exposure=max_exposure)
    print(f"  Filter 4 (Exposure Cap {max_exposure*100:.0f}%):")
    print(f"    Total meaningful tokens: {total_tokens:,}")
    print(f"    Max allowed occurrences: {f4_stats.get('max_bigram_occurrences', 0):,}")
    print(f"    Kept bigram types:       {f4_stats.get('kept_bigrams', 0):,}")
    print(f"    Dropped bigram types:    {f4_stats.get('dropped_bigrams', 0):,}")
    print(f"    Kept occurrences:        {f4_stats.get('kept_occurrences', 0):,}")
    print(f"    Final exposure ratio:    {f4_stats.get('final_exposure', 0.0):.4f}")

    # --- SAVE BIGRAMS TO TSV ---
    tsv_path = output_dir / f"{volume_name}.tsv"
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write("bigram\tcount\tpmi\n")
        for bigram, b_count, pmi in final_results:
            f.write(f"{bigram}\t{b_count}\t{pmi}\n")
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
        'total_tokens':         len(words),
        'unique_unigrams':      len(unigram_counts),
        'unique_bigrams':       len(final_results),
        'unigram_mb':           round(unigram_mb,  3),
        'bigram_mb':            round(bigram_mb,   3),
        'bigram_growth':        round(bigram_growth,   2),
        'bigram_singleton_pct':   round(bigram_singleton_pct,   1),
    }


# ---------------------------------------------------------------------------
# Main (HPC Entrypoint)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract Ngrams from massive HathiTrust Pairtree storage on HPC clusters.")
    parser.add_argument("--pairtree", required=True, type=Path, help="Absolute path to the HathiTrust Pairtree root (e.g., /N/project/htrc/data/ht_text)")
    parser.add_argument("--id-list", required=True, type=Path, help="Absolute path to a text file containing HathiTrust Volume IDs (one per line)")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum volume-level frequency threshold for Filter 2 (default: 2)")
    parser.add_argument("--min-pmi", type=float, default=0.0, help="Minimum PMI threshold for Filter 3 (default: 0.0)")
    parser.add_argument("--max-exposure", type=float, default=0.15, help="Maximum token exposure ratio for Filter 4 (default: 0.15)")
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

    export_dir = Path.cwd() / "exported_bigrams"
    export_dir.mkdir(exist_ok=True)
    print(f"Export Directory configured at: {export_dir}")

    with open(htids_file, 'r', encoding='utf-8') as f:
        htids = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
    if len(htids) == 0:
        print(f"❌ The ID list file is empty!")
        return

    print(f"Loaded {len(htids)} Volume IDs to process.")
    print("=" * 70)

    all_results = []
    
    for htid in htids:
        zip_path = htid_to_zip_path(htid, pairtree_root)
        
        if not zip_path.exists():
            print(f"\nAnalyzing: {htid}")
            print(f"  ❌ ERROR: Zip file not found for mathematically generated path:\n  {zip_path}")
            continue
            
        try:
            result = analyze_volume(zip_path, export_dir, min_freq=args.min_freq, min_pmi=args.min_pmi, max_exposure=args.max_exposure)
            if result:
                result['htid'] = htid
                all_results.append(result)
        except Exception as e:
            print(f"  ❌ ERROR analyzing {zip_path.name}: {e}")

    if len(all_results) == 0:
        print("\n❌ No volumes successfully analyzed. Exiting.")
        return

    script_dir = Path(__file__).parent
    csv_path = script_dir / 'bigred_results.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
        writer.writeheader()
        writer.writerows(all_results)

    print("\n" + "=" * 70)
    print(f"✅ Fast-track HPC analysis complete! Results saved to: {csv_path}")

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
