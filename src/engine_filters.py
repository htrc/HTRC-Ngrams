import math
from collections import Counter

import stopwordsiso
from nltk.corpus import stopwords

class UnsupportedLanguageError(Exception):
    pass



STOPWORD_LANGUAGE_ALIASES = {
    "ger": "deu",
    "fre": "fra",
    "chi": "zho",
    "ces": "cze",
}


STOPWORD_PROFILES = {
    # NLTK stopword lists
    "eng": {"source": "nltk", "code": "english"},
    "deu": {"source": "nltk", "code": "german"},
    "fra": {"source": "nltk", "code": "french"},
    "spa": {"source": "nltk", "code": "spanish"},
    "zho": {"source": "nltk", "code": "chinese"},
    "rus": {"source": "nltk", "code": "russian"},
    "ita": {"source": "nltk", "code": "italian"},
    "por": {"source": "nltk", "code": "portuguese"},
    "ara": {"source": "nltk", "code": "arabic"},
    "dut": {"source": "nltk", "code": "dutch"},
    "swe": {"source": "nltk", "code": "swedish"},
    "heb": {"source": "nltk", "code": "hebrew"},
    "dan": {"source": "nltk", "code": "danish"},
    "ind": {"source": "nltk", "code": "indonesian"},
    "hun": {"source": "nltk", "code": "hungarian"},

    # stopwords-iso fallback lists
    "jpn": {"source": "stopwords-iso", "code": "ja"},
    "lat": {"source": "stopwords-iso", "code": "la"},
    "pol": {"source": "stopwords-iso", "code": "pl"},
    "kor": {"source": "stopwords-iso", "code": "ko"},
    "cze": {"source": "stopwords-iso", "code": "cs"},
}


def canonicalize_stopword_language(lang: str) -> str:
    return STOPWORD_LANGUAGE_ALIASES.get(lang, lang)


def get_stopword_profile(lang: str) -> dict:
    canonical_lang = canonicalize_stopword_language(lang)

    profile = STOPWORD_PROFILES.get(canonical_lang)
    if profile is None:
        raise UnsupportedLanguageError(lang)

    return profile


def get_stopword_metadata(lang: str) -> dict:
    profile = get_stopword_profile(lang)
    return {
        "source": profile["source"],
        "list_code": profile["code"],
    }


def get_standard_stopwords(lang: str) -> set[str]:
    profile = get_stopword_profile(lang)
    source = profile["source"]
    language_code = profile["code"]

    if source == "nltk":
        try:
            words = stopwords.words(language_code)
        except LookupError as error:
            raise LookupError(
                "The NLTK stopwords corpus is not installed. "
                "Run: python -m nltk.downloader stopwords"
            ) from error
    else:
        words = stopwordsiso.stopwords(language_code)

    if not words:
        raise RuntimeError(
            f"Stopword source '{source}' returned no words for '{lang}'."
        )

    return {
        word.casefold()
        for word in words
        if word and word.strip()
    }


def get_stopword_source(lang: str) -> str:
    return get_stopword_profile(lang)["source"]
def select_top_standard_stopwords(unigram_counts, lang: str, top_n: int = 25):
    """Select the most frequent tokens that are in the standard stopword list."""
    standard_stopwords = get_standard_stopwords(lang)
    folded_counts = Counter()

    for token, count in unigram_counts.items():
        folded = token.casefold()
        if folded in standard_stopwords:
            folded_counts[folded] += count

    selected = dict(folded_counts.most_common(top_n))
    return set(selected.keys()), selected

def filter_1_standard_stopword_bigrams(bigram_counts, selected_stopwords):
    """
    Filter 1: Stopword Removal.
    Remove bigrams where BOTH words are in the selected top standard stopwords.
    """
    filtered_bigrams = Counter()
    dropped_bigrams = Counter()

    for bigram, count in bigram_counts.items():
        word1, word2 = bigram
        is_stopword_bigram = (
            word1.casefold() in selected_stopwords
            and word2.casefold() in selected_stopwords
        )

        if is_stopword_bigram:
            dropped_bigrams[bigram] = count
        else:
            filtered_bigrams[bigram] = count

    return filtered_bigrams, dropped_bigrams

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
        w1, w2 = bigram

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
