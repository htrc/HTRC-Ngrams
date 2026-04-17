import re
from collections import Counter
from .config import LANGUAGE_PROFILES

def resolve_language(htid: str, pages: list):
    """
    Determine the optimal language profile for the volume.
    First checks metadata, then falls back to langid sampling.
    Returns 'default_latin' if detection fails.
    """
    print(f"  [Language Resolver] Initializing Language Resolution for '{htid}'...")
    
    # --- STAGE 1: METADATA CHECK ---
    # TODO: Load active cluster JSON/CSV metadata here using htid matching
    metadata_lang = None
    if metadata_lang:
        print(f"  [Language Resolver] ⚡ STAGE 1 SUCCESS: Mapped cluster metadata code '{metadata_lang}'")
        return metadata_lang
        
    print(f"  [Language Resolver] ❌ STAGE 1 MISS: No metadata array matched. Transitioning to STAGE 2 Detector...")

    # --- FALLBACK: langid DETECTION ---
    # Sample 10 pages from the interior of the text to avoid frontmatter
    sample_text = " ".join(pages[20:30]).lower() if len(pages) > 30 else " ".join(pages).lower()
    
    try:
        import langid
        langid.set_languages(['en', 'fr', 'es', 'de'])
        iso_code, confidence = langid.classify(sample_text)
        
        marc_map = {'en': 'eng', 'fr': 'fra', 'es': 'spa', 'de': 'deu'}
        best_lang = marc_map.get(iso_code, iso_code)
        
        print(f"  [Language Resolver] 🔍 STAGE 2 SUCCESS: 'langid' ML profiled base text as '{best_lang}' (Confidence: {confidence:.2f})")
        return best_lang
        
    except ImportError:
        print(f"  [Language Resolver] ⚠️ STAGE 2 MISS: 'langid' module not installed on cluster. Engaging STAGE 3 Failsafe...")
        return "default_latin"

def get_profile(lang: str):
    """Retrieve the language execution profile, falling back to safe limits."""
    if lang in LANGUAGE_PROFILES:
        return LANGUAGE_PROFILES[lang]
    return LANGUAGE_PROFILES["default_latin"]

def is_header_footer(line: str, profile: dict) -> bool:
    """Evaluate if a line matches specific OCR/Header rejection criteria."""
    line_clean = line.strip()
    if not line_clean:
        return False
        
    upper_line = line_clean.upper()
    
    digit_punct = re.findall(r'[\d\-\.\[\]]', line_clean)
    if digit_punct:
        non_space_len = len(line_clean.replace(" ", ""))
        if non_space_len > 0 and len(digit_punct) / non_space_len >= 0.7:
            return True
            
    if re.match(r'^[MDCLXVI]+$', upper_line.strip('. ')):
        return True
        
    if profile["header_keywords"]:
        keywords_pattern = "|".join(profile["header_keywords"])
        if re.match(fr'^({keywords_pattern})\b', upper_line):
            return True
        
    words = upper_line.split()
    if 1 <= len(words) <= 3 and len(line_clean) < 20:
        if re.search(r'[A-Z]', upper_line) and line_clean == upper_line:
            return True
            
    return False

def tokenize_page(text: str, profile: dict):
    """Clean, line-strip, and token-merge a single page, capturing ' and -."""
    if profile["script"] == "cjk":
        return []

    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text, flags=re.UNICODE)
    
    lines = text.split('\n')
    if len(lines) >= 8:
        if lines and is_header_footer(lines[0], profile):
            lines = lines[1:]
        if lines and is_header_footer(lines[-1], profile):
            lines = lines[:-1]
            
    text = ' '.join(lines).lower()
    
    raw_tokens = re.findall(r"[^\W\d_]+(?:['\-][^\W\d_]+)*", text, flags=re.UNICODE)
    
    final_tokens = []
    whitelist_chars = profile["allowed_single_chars"]
    
    for token in raw_tokens:
        token = token.replace("'", "").replace("-", "")
        if len(token) == 1 and token not in whitelist_chars:
            continue
        final_tokens.append(token)
        
    return final_tokens

def generate_ngrams_with_counts(words, n):
    """Generate n-grams and return Counter object."""
    ngrams = []
    for i in range(len(words) - n + 1):
        ngram = ' '.join(words[i:i+n])
        ngrams.append(ngram)
    return Counter(ngrams)
