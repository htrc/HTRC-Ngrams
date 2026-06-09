import re
from collections import Counter
from .config import LANGUAGE_PROFILES

class LowConfidenceLanguageError(RuntimeError):
    """
    Raised when langid's confidence score is too low to trust.
    Volume is skipped and logged to pending_volumes.txt for retry
    once EF metadata covers it (Blocker 7 Stage 1 fix).
    """

def get_lang_from_jsonld(jsonld_path) -> str:
    """
    Extract language code from a HathiTrust JSON-LD Bibframe file.
    Files are bz2-compressed. Uses regex to find the language code
    regardless of how deeply nested the RDF graph structure is.
    Returns None if field not found.
    """
    try:
        import bz2, re
        with bz2.open(jsonld_path, 'rt', encoding='utf-8') as f:
            content = f.read()
            
        # 1. Standard: "language" : "eng"
        m1 = re.search(r'"language"\s*:\s*"([a-z]{3})"', content)
        if m1: return m1.group(1)
            
        # 2. Array (single or multiple): "language" : [ "eng", "gle" ]
        # By removing the strict closing bracket `\]`, we just grab the very first language code in the array.
        m2 = re.search(r'"language"\s*:\s*\[\s*"([a-z]{3})"', content)
        if m2: return m2.group(1)
        
        # 3. RDF Value: "language": [{"@value": "eng"}]
        m3 = re.search(r'"language"\s*:\s*\[\s*\{\s*"@value"\s*:\s*"([a-z]{3})"', content)
        if m3: return m3.group(1)

        # 4. RDF Node: "language": "http://id.loc.gov/vocabulary/languages/eng"
        m4 = re.search(r'"language"\s*:\s*".*?/languages/([a-z]{3})"', content)
        if m4: return m4.group(1)
            
    except Exception as e:
        return f"ERROR: {e}"
    return None

def resolve_language(htid: str, pages: list, ef_root=None):
    """
    Determine the optimal language profile for the volume.
    First checks metadata, then falls back to langid sampling.
    Returns 'default_latin' if detection fails.
    """
    print(f"  [Language Resolver] Initializing for '{htid}'...")
    
    # --- STAGE 1: JSON-LD METADATA (EF Dataset) ---
    if ef_root is not None:
        from .engine_io import find_jsonld_path
        jsonld_path = find_jsonld_path(htid, ef_root)
        if jsonld_path:
            print(f"  [Language Resolver] 🔎 STAGE 1: Found file at {jsonld_path}, attempting to extract language...")
            metadata_lang = get_lang_from_jsonld(jsonld_path)
            if metadata_lang and not metadata_lang.startswith("ERROR:"):
                print(f"  [Language Resolver] ⚡ STAGE 1 SUCCESS: JSON-LD metadata → '{metadata_lang}'")
                return metadata_lang
            else:
                err_msg = metadata_lang if metadata_lang else "'language' key missing"
                print(f"  [Language Resolver] ❌ STAGE 1 MISS: File found, but {err_msg}.")
        else:
            print(f"  [Language Resolver] ❌ STAGE 1 MISS: No physical JSON-LD file found for {htid}.")
        print(f"  [Language Resolver] Moving to Stage 2...")

    # --- FALLBACK: langid DETECTION ---
    # Sample pages 40-60 to skip title pages, prefaces, and multi-language frontmatter.
    # For short books (< 60 pages), use the middle third as the safest approximation.
    sample_text = " ".join(pages[40:60]).lower() if len(pages) > 60 else " ".join(pages[len(pages)//3:]).lower()
    
    try:
        import langid

        # Lock the classifier to our 10 supported languages.
        # Prevents misclassification into micro-dialects (e.g. German -> Luxembourgish).
        langid.set_languages(['en', 'fr', 'es', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar'])
        iso_code, confidence = langid.classify(sample_text)

        # Confidence is a log-probability sum, which grows negatively with text length.
        # We must normalize it by the length of the sample text to get a per-character score.
        text_len = max(len(sample_text), 1)
        normalized_confidence = confidence / text_len

        # A typical normalized score for a valid language is between -5.0 and -15.0.
        # Anything below -25.0 per character is highly likely to be OCR noise or gibberish.
        CONFIDENCE_THRESHOLD_PER_CHAR = -25.0
        if normalized_confidence < CONFIDENCE_THRESHOLD_PER_CHAR:
            raise LowConfidenceLanguageError(
                f"langid confidence too low ({normalized_confidence:.1f} per char) to safely determine language. "
                f"Best guess was '{iso_code}' (Raw Score: {confidence:.1f}) but cannot be trusted."
            )

        # Map ISO 639-1 (2-letter) codes to HathiTrust MARC (3-letter) codes
        marc_map = {
            'en': 'eng',  # English
            'fr': 'fra',  # French
            'es': 'spa',  # Spanish
            'de': 'deu',  # German
            'it': 'ita',  # Italian
            'pt': 'por',  # Portuguese
            'ru': 'rus',  # Russian
            'zh': 'zho',  # Chinese
            'ja': 'jpn',  # Japanese
            'ar': 'ara',  # Arabic
        }
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
        if line_clean.isupper():
            return True
            
    return False

def tokenize_page(text: str, profile: dict, lang: str = ""):
    """Clean, line-strip, and token-merge a single page, capturing ' and -."""
    if profile["script"] == "cjk":
        # Handle Chinese tokenization using jieba
        if lang == "zho":
            try:
                import jieba  # pyrefly: ignore [missing-import]
                clean_text = re.sub(r'\s+', '', text)
                tokens = jieba.cut(clean_text)
                # Keep only tokens that are purely CJK characters (Unicode range U+4E00–U+9FFF)
                # This filters out Latin OCR noise mixed into Chinese scans
                return [t for t in tokens if t and re.fullmatch(r'[\u4e00-\u9fff]+', t)]
            except ImportError:
                raise ImportError("jieba (Chinese NLP segmenter)")
        # Handle Japanese tokenization using janome
        elif lang == "jpn":
            try:
                from janome.tokenizer import Tokenizer  # pyrefly: ignore [missing-import]
                t = Tokenizer()
                clean_text = re.sub(r'\s+', '', text)
                tokens = [tok.surface for tok in t.tokenize(clean_text)]
                # Keep only tokens containing CJK, Hiragana, or Katakana characters
                # Filters out punctuation and Latin OCR noise
                return [tok for tok in tokens if tok and re.search(r'[\u3040-\u9fff]', tok)]
            except ImportError:
                raise ImportError("janome (Japanese NLP segmenter)")
        else:
            return []
            
    # Arabic: right-to-left, complex ligature system — Latin regex produces garbage.
    if profile["script"] == "arabic":
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
