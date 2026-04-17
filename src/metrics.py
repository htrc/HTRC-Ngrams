import json

def estimate_json_size(ngram_dict):
    """Estimate storage size in MB for JSON format."""
    if len(ngram_dict) == 0:
        return 0.0
    sample_size = min(1000, len(ngram_dict))
    sample = dict(list(ngram_dict.items())[:sample_size])
    avg_bytes_per_entry = len(json.dumps(sample)) / sample_size
    total_bytes = avg_bytes_per_entry * len(ngram_dict)
    return total_bytes / (1024 * 1024)

def singleton_rate(ngram_dict):
    """Return percentage of n-grams that appear only once."""
    if len(ngram_dict) == 0:
        return 0.0
    singletons = sum(1 for count in ngram_dict.values() if count == 1)
    return (singletons / len(ngram_dict)) * 100
