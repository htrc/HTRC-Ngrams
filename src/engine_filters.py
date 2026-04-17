import math
from collections import Counter

def filter1_top25(bigram_counts, unigram_counts):
    """
    Filter 1: Stopword Removal.
    Remove bigrams consisting entirely of the volume's 25 most frequent tokens.
    Dynamically generated per volume to handle multilingual datasets.
    """
    top25_words = [word for word, count in unigram_counts.most_common(25)]
    top25 = set(top25_words)
    filtered_bigrams = Counter()
    
    for bigram, count in bigram_counts.items():
        try:
            w1, w2 = bigram.split(' ', 1)
        except ValueError:
            continue
        # Keep if at least ONE word is NOT in the top 25
        if w1 not in top25 or w2 not in top25:
            filtered_bigrams[bigram] = count
            
    return filtered_bigrams, top25_words

def filter2_min_frequency(bigram_counts, min_freq=2):
    """
    Filter 2: Minimum Frequency Threshold.
    Removes bigrams appearing fewer than min_freq times in the volume. 
    Reduces the retention of OCR artifacts and unique sentence fragments.
    """
    filtered_bigrams = Counter()
    removed_distinct_count = 0
    removed_occurrences = 0
    
    for bigram, count in bigram_counts.items():
        if count >= min_freq:
            filtered_bigrams[bigram] = count
        else:
            removed_distinct_count += 1
            removed_occurrences += count
            
    return filtered_bigrams, removed_distinct_count, removed_occurrences

def filter3_pmi(surviving_bigrams, unigram_counts, total_bigrams, total_tokens, min_pmi=0.0):
    """
    Filter 3: Pointwise Mutual Information (PMI).
    Calculates PMI using base volume token frequencies.
    Filters out independent or negative collocations based on min_pmi threshold.
    Returns: list of (bigram, count, pmi) sorted by count.
    """
    scored_bigrams = []
    dropped_count = 0
    
    for bigram, b_count in surviving_bigrams.items():
        try:
            w1, w2 = bigram.split(' ', 1)
        except ValueError:
            continue
            
        p_bigram = b_count / total_bigrams if total_bigrams > 0 else 0
        p_w1 = unigram_counts[w1] / total_tokens if total_tokens > 0 else 0
        p_w2 = unigram_counts[w2] / total_tokens if total_tokens > 0 else 0
        
        if p_w1 == 0 or p_w2 == 0 or p_bigram == 0:
            continue
            
        pmi = math.log2(p_bigram / (p_w1 * p_w2))
        
        if pmi >= min_pmi:
            scored_bigrams.append((bigram, b_count, round(pmi, 3)))
        else:
            dropped_count += 1
            
    # Sort predominantly by Frequency, with PMI as the secondary rank
    scored_bigrams.sort(key=lambda x: (x[1], x[2]), reverse=True)
    
    return scored_bigrams, dropped_count

def filter4_exposure_cap(scored_results, total_tokens, max_exposure=0.15):
    """
    Filter 4: Volume Token Exposure Cap.
    Trims the dataset to ensure total bigram token coverage does not exceed 
    the max_exposure percentage of the original volume's token count.
    """
    if total_tokens <= 0:
        return [], {}

    max_bigram_occurrences = int((max_exposure * total_tokens) / 2)
    
    kept = []
    running_occurrences = 0
    
    for row in scored_results:
        count = row[1] 
        
        if count <= 0:
            continue
            
        if running_occurrences + count <= max_bigram_occurrences:
            kept.append(row)
            running_occurrences += count
        else:
            break
            
    final_exposure = 0.0
    if total_tokens > 0:
        final_exposure = (2 * running_occurrences) / total_tokens
        
    stats = {
        "max_bigram_occurrences": max_bigram_occurrences,
        "kept_bigrams": len(kept),
        "dropped_bigrams": len(scored_results) - len(kept),
        "kept_occurrences": running_occurrences,
        "final_exposure": final_exposure,
    }
    
    return kept, stats
