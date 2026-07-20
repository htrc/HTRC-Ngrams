import re
import os
from collections import Counter
from .config import LANGUAGE_PROFILES

_STANZA_PIPELINES = {}

STANZA_LANG_MAP = {
    "eng": "en",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "zho": "zh-hans",
    "chi": "zh-hans",
    "jpn": "ja",
    "rus": "ru",
    "ita": "it",
    "por": "pt",
    "ara": "ar",
    "lat": "la",
    "dut": "nl",
    "pol": "pl",
    "swe": "sv",
    "heb": "he",
    "kor": "ko",
    "cze": "cs",
    "dan": "da",
    "ind": "id",
    "hun": "hu",
}

STANZA_MWT_LANGS = {
    "en", "de", "fr", "es", "it", "pt", "ar",
    "la", "nl", "pl", "he", "cs", "id"
}

def get_stanza_pipeline(lang: str):
    import stanza
    from stanza.pipeline.core import DownloadMethod

    stanza_lang = STANZA_LANG_MAP.get(lang, lang)

    if stanza_lang in _STANZA_PIPELINES:
        return _STANZA_PIPELINES[stanza_lang]

    processors = "tokenize,mwt" if stanza_lang in STANZA_MWT_LANGS else "tokenize"
    model_dir = os.environ.get("STANZA_RESOURCES_DIR")

    kwargs = {
        "lang": stanza_lang,
        "processors": processors,
        "download_method": DownloadMethod.REUSE_RESOURCES,
        "verbose": False,
    }
    if model_dir:
        kwargs["dir"] = model_dir

    nlp = stanza.Pipeline(**kwargs)

    _STANZA_PIPELINES[stanza_lang] = nlp
    return nlp

def separate_inline_hyphens(text: str) -> str:
    return re.sub(r"(?<=[^\W_])-(?=[^\W_])", " - ", text, flags=re.UNICODE)

def tokenize_with_stanza(text: str, lang: str):
    nlp = get_stanza_pipeline(lang)
    doc = nlp(text)

    tokens = []
    for sentence in doc.sentences:
        if sentence.words:
            tokens.extend(word.text for word in sentence.words)
        else:
            tokens.extend(token.text for token in sentence.tokens)

    return [token for token in tokens if token and token.strip()]
class LowConfidenceLanguageError(RuntimeError):
    """
    Raised when langid's confidence score is too low to trust.
    Volume is skipped and logged to pending_volumes.txt for retry
    once EF metadata covers it (Blocker 7 Stage 1 fix).
    """

SCANNER_BOILERPLATE_MARKERS = [
    "internet archive",
    "archive.org",
    "digitized by google",
    "hathitrust digital library",
    "google books",
    "canadian institute for historical microreproductions",
    "historical microreproductions",
    "institut canadien de microreproductions",
    "microfilm edition",
    "photographic sciences corporation",
    "image evaluation",
    "bibliographically unique",
    "tight binding",
    "cause shadows",
    "original copies",
    "images appearing",
]
def is_scanner_boilerplate_page(text: str) -> bool:
    """Return True for scanner/QC pages that should not create n-grams."""
    text_lower = text.lower()
    return any(marker in text_lower for marker in SCANNER_BOILERPLATE_MARKERS)



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

LANGUAGE_ALIASES = {
    "chi": "zho",
    "ces": "cze",
}

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
                metadata_lang = LANGUAGE_ALIASES.get(metadata_lang, metadata_lang)
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

        # Lock the classifier to our supported languages.
        # Prevents misclassification into micro-dialects (e.g. German -> Luxembourgish).
        langid.set_languages(['en', 'fr', 'es', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar',
                              'la', 'nl', 'pl', 'sv', 'he',
                              'ko', 'cs', 'da', 'id', 'hu'])
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
            'la': 'lat',  # Latin
            'nl': 'dut',  # Dutch
            'pl': 'pol',  # Polish
            'sv': 'swe',  # Swedish
            'he': 'heb',  # Hebrew
            'ko': 'kor',  # Korean
            'cs': 'cze',  # Czech
            'da': 'dan',  # Danish
            'id': 'ind',  # Indonesian
            'hu': 'hun',  # Hungarian
        }
        best_lang = marc_map.get(iso_code, iso_code)

        print(f"  [Language Resolver] 🔍 STAGE 2 SUCCESS: 'langid' ML profiled base text as '{best_lang}' (Confidence: {confidence:.2f})")
        return best_lang

    except ImportError:
        print(f"  [Language Resolver] ⚠️ STAGE 2 MISS: 'langid' module not installed on cluster. Engaging STAGE 3 Failsafe...")
        return "default_latin"

def get_profile(lang: str):
    """Retrieve the language execution profile, falling back to safe limits."""
    lang = LANGUAGE_ALIASES.get(lang, lang)
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

    if is_scanner_boilerplate_page(text):
        return []

    lines = text.splitlines()

    while lines and not lines[0].strip():
        lines.pop(0)
    
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) >= 8:
        for _ in range(2):
            if lines and is_header_footer(lines[0], profile):
                lines = lines[1:]
            else:
                break
        for _ in range(2):
            if lines and is_header_footer(lines[-1], profile):
                lines = lines[:-1]
            else:
                break
                
    text = '\n'.join(lines)

    # --- CJK Tokenization logic commented out for Stanza test ---
    # if profile.get("script") == "korean":
    #     text = text.replace("\n", " ")
    #     return re.findall(r"[\u1100-\u11FF\u3130-\u318F\uA960-\uA97F\uAC00-\uD7A3\uD7B0-\uD7FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+", text)
    #
    # if profile.get("script") == "cjk":
    #     if lang == "zho":
    #         try:
    #             import jieba  # pyrefly: ignore [missing-import]
    #             clean_text = re.sub(r'\s+', '', text)
    #             tokens = jieba.cut(clean_text)
    #             return [t for t in tokens if t and re.fullmatch(r'[\u4e00-\u9fff]+', t)]
    #         except ImportError:
    #             raise ImportError("jieba (Chinese NLP segmenter)")
    #     elif lang == "jpn":
    #         try:
    #             from janome.tokenizer import Tokenizer  # pyrefly: ignore [missing-import]
    #             t = Tokenizer()
    #             clean_text = re.sub(r'\s+', '', text)
    #             tokens = [tok.surface for tok in t.tokenize(clean_text)]
    #             return [tok for tok in tokens if tok and re.search(r'[\u3040-\u9fff]', tok)]
    #         except ImportError:
    #             raise ImportError("janome (Japanese NLP segmenter)")
    #     else:
    #         return []
    #
    # if profile["script"] == "arabic":
    #     return []

    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text, flags=re.UNICODE)

    # Strip Unicode superscript (², ³, ¹, ⁰–⁹) and subscript (₀–₉) characters.
    text = re.sub(r'[\u00B2\u00B3\u00B9\u2070-\u2079\u207A-\u207F\u2080-\u2089]', '', text)

    # Strip zero-width invisible characters
    text = re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', text)

    if profile.get("script") == "hebrew":
        # Strip Hebrew vowels (Niqqud) and cantillation marks
        text = re.sub(r'[\u0591-\u05C7]', '', text)

    # Force inline hyphens to be detached so Stanza splits them reliably
    text = separate_inline_hyphens(text)

    # Let Stanza handle Tokenization directly
    final_tokens = tokenize_with_stanza(text, lang)

    return final_tokens

def generate_ngrams_with_counts(words, n):
    """Generate n-grams and return Counter object."""
    ngrams = []
    if n == 1:
        return Counter((token,) for token in words)

    for i in range(len(words) - n + 1):
        ngram = tuple(words[i:i+n])
        ngrams.append(ngram)
    return Counter(ngrams)
