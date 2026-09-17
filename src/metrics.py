def singleton_rate(ngram_dict):
    """Return percentage of n-grams that appear only once."""
    if len(ngram_dict) == 0:
        return 0.0
    singletons = sum(1 for count in ngram_dict.values() if count == 1)
    return (singletons / len(ngram_dict)) * 100
