import re
import os
from collections import Counter
from .config import LANGUAGE_PROFILES
from .engine_io import MetadataAccessError, MetadataPermissionError

_STANZA_PIPELINES = {}

STANZA_PAGE_BATCH_SIZE = 50

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
    "hin": "hi",
    "hrv": "hr",
    "tur": "tr",
    "nor": "no",
    "tha": "th",
    "vie": "vi",
    "urd": "ur",
}

STANZA_MWT_LANGS = {
    "en", "de", "fr", "es", "it", "pt", "ar",
    "la", "nl", "pl", "he", "cs", "id", "tr"
}

LANGUAGE_ALIASES = {
    "chi": "zho",
    "zho": "zho",
    "ger": "deu",
    "deu": "deu",
    "fre": "fra",
    "fra": "fra",
    "ces": "cze",
    "cze": "cze",
    "scr": "hrv",
    "hrv": "hrv",
}


def canonicalize_language(lang: str) -> str:
    """Normalize supported bibliographic aliases to one internal code."""
    normalized = (lang or "").strip().lower()
    return LANGUAGE_ALIASES.get(normalized, normalized)


SUPPORTED_LANGUAGE_CODES = frozenset(
    code
    for code in LANGUAGE_PROFILES
    if code in STANZA_LANG_MAP
)


def is_supported_language(lang: str) -> bool:
    return canonicalize_language(lang) in SUPPORTED_LANGUAGE_CODES


class UnsupportedStanzaLanguageError(RuntimeError):
    def __init__(self, language: str):
        self.language = language
        super().__init__(f"No Stanza language mapping is configured for '{language}'.")


class UnsupportedLanguageResolutionError(RuntimeError):
    def __init__(self, language: str, source: str):
        self.language = canonicalize_language(language) or "unknown"
        self.source = source
        super().__init__(
            f"Language '{language}' from {source} is not supported by the pipeline."
        )


class StanzaPipelineInitializationError(RuntimeError):
    def __init__(self, language: str, stanza_language: str, detail: str):
        self.language = language
        self.stanza_language = stanza_language
        super().__init__(
            f"Could not initialize Stanza '{stanza_language}' for '{language}': {detail}"
        )

def get_stanza_pipeline(lang: str):
    import stanza
    from stanza.pipeline.core import DownloadMethod

    lang = canonicalize_language(lang)
    if lang not in STANZA_LANG_MAP:
        raise UnsupportedStanzaLanguageError(lang)

    stanza_lang = STANZA_LANG_MAP[lang]

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

    try:
        nlp = stanza.Pipeline(**kwargs)
    except Exception as error:
        raise StanzaPipelineInitializationError(
            lang, stanza_lang, str(error)
        ) from error

    _STANZA_PIPELINES[stanza_lang] = nlp
    return nlp

def separate_inline_hyphens(text: str) -> str:
    return re.sub(r"(?<=[^\W_])-(?=[^\W_])", " - ", text, flags=re.UNICODE)


def separate_vietnamese_sentence_punctuation(text: str) -> str:
    return re.sub(r"([.!?。！？]+)(?=\s|$)", r" \1 ", text)


def tokens_from_stanza_document(doc):
    """Return the word-token sequence from one Stanza document."""
    tokens = []
    for sentence in doc.sentences:
        if sentence.words:
            tokens.extend(word.text for word in sentence.words)
        else:
            tokens.extend(token.text for token in sentence.tokens)

    return [token for token in tokens if token and token.strip()]


def tokenize_with_stanza(text: str, lang: str):
    """Single-document helper retained for tokenizer validation."""
    nlp = get_stanza_pipeline(lang)
    return tokens_from_stanza_document(nlp(text))


def tokenize_pages_with_stanza(page_texts: list[str], lang: str):
    """Tokenize multiple cleaned pages while keeping each page separate."""
    if not page_texts:
        return []

    nlp = get_stanza_pipeline(lang)
    documents = nlp.bulk_process(page_texts)
    if len(documents) != len(page_texts):
        raise RuntimeError(
            "Stanza returned a different number of documents than input pages."
        )

    return [tokens_from_stanza_document(document) for document in documents]


class LowConfidenceLanguageError(RuntimeError):
    """
    Raised when langid's confidence score is too low to trust.
    The volume is recorded in the task-specific pending log for review.
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
        import bz2
        with bz2.open(jsonld_path, 'rt', encoding='utf-8') as f:
            content = f.read()

        # 1. Standard: "language" : "eng"
        m1 = re.search(r'"language"\s*:\s*"([a-z]{3})"', content, re.IGNORECASE)
        if m1: return m1.group(1).lower()

        # 2. Array (single or multiple): "language" : [ "eng", "gle" ]
        # By removing the strict closing bracket `\]`, we just grab the very first language code in the array.
        m2 = re.search(r'"language"\s*:\s*\[\s*"([a-z]{3})"', content, re.IGNORECASE)
        if m2: return m2.group(1).lower()

        # 3. RDF Value: "language": [{"@value": "eng"}]
        m3 = re.search(r'"language"\s*:\s*\[\s*\{\s*"@value"\s*:\s*"([a-z]{3})"', content, re.IGNORECASE)
        if m3: return m3.group(1).lower()

        # 4. RDF Node: "language": "http://id.loc.gov/vocabulary/languages/eng"
        m4 = re.search(r'"language"\s*:\s*".*?/languages/([a-z]{3})"', content, re.IGNORECASE)
        if m4: return m4.group(1).lower()

    except PermissionError as error:
        raise MetadataPermissionError(jsonld_path, str(error)) from error
    except Exception as error:
        raise MetadataAccessError(jsonld_path, str(error)) from error
    return None

def resolve_language_from_metadata(htid: str, ef_root=None):
    """Return a supported metadata language, or None when metadata is absent."""
    if ef_root is None:
        return None

    from .engine_io import find_jsonld_path

    jsonld_path = find_jsonld_path(htid, ef_root)
    if jsonld_path is None:
        return None

    metadata_lang = get_lang_from_jsonld(jsonld_path)
    if not metadata_lang:
        return None

    canonical_lang = canonicalize_language(metadata_lang)
    if not is_supported_language(canonical_lang):
        raise UnsupportedLanguageResolutionError(
            metadata_lang, "JSON-LD metadata"
        )

    return canonical_lang


def resolve_language(htid: str, pages: list, ef_root=None):
    """
    Determine the optimal language profile for the volume.
    First checks metadata, then falls back to langid sampling.
    Returns a canonical supported language code or raises a typed error.
    """
    metadata_lang = resolve_language_from_metadata(htid, ef_root)
    if metadata_lang is not None:
        return metadata_lang

    # --- FALLBACK: langid DETECTION ---
    # Sample pages 40-60 for long books. Short books use all available pages so
    # that language detection is not based on only one title or contents page.
    sample_pages = pages[40:60] if len(pages) > 60 else pages
    sample_text = " ".join(
        page for page in sample_pages if page and page.strip()
    ).lower()

    # If the interior window of a long volume is blank, retry with every page
    # available in the language-sampling pass.
    if not sample_text.strip() and len(pages) > 60:
        sample_text = " ".join(
            page for page in pages if page and page.strip()
        ).lower()

    if not sample_text.strip():
        raise LowConfidenceLanguageError(
            "No non-empty text was available for language detection."
        )

    try:
        import langid

        # Lock the classifier to our supported languages.
        # Prevents misclassification into micro-dialects (e.g. German -> Luxembourgish).
        langid.set_languages(['en', 'fr', 'es', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar',
                              'la', 'nl', 'pl', 'sv', 'he',
                              'ko', 'cs', 'da', 'id', 'hu',
                              'hi', 'hr', 'tr', 'no', 'th', 'vi', 'ur'])
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
            'hi': 'hin',  # Hindi
            'hr': 'hrv',  # Croatian
            'tr': 'tur',  # Turkish
            'no': 'nor',  # Norwegian
            'th': 'tha',  # Thai
            'vi': 'vie',  # Vietnamese
            'ur': 'urd',  # Urdu
        }
        best_lang = canonicalize_language(marc_map.get(iso_code, iso_code))

        if not is_supported_language(best_lang):
            raise UnsupportedLanguageResolutionError(best_lang, "langid")

        return best_lang

    except ImportError as error:
        raise ImportError(
            "The langid package is required when usable language metadata is unavailable."
        ) from error

def get_profile(lang: str):
    """Retrieve a canonical supported language execution profile."""
    canonical_lang = canonicalize_language(lang)
    if not is_supported_language(canonical_lang):
        raise UnsupportedLanguageResolutionError(lang, "language profile lookup")
    return LANGUAGE_PROFILES[canonical_lang]

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

def prepare_page_text(text: str, profile: dict, lang: str = "") -> str:
    """Apply page-level cleanup before language-specific tokenization."""

    if is_scanner_boilerplate_page(text):
        return ""

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

    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text, flags=re.UNICODE)

    # Strip Unicode superscript (², ³, ¹, ⁰–⁹) and subscript (₀–₉) characters.
    text = re.sub(r'[\u00B2\u00B3\u00B9\u2070-\u2079\u207A-\u207F\u2080-\u2089]', '', text)

    # Strip zero-width invisible characters
    text = re.sub(r'[\u200B\u200C\u200D\uFEFF]', '', text)

    if profile.get("script") == "hebrew":
        # Strip Hebrew vowels (niqqud) and cantillation marks while preserving
        # punctuation such as maqaf (U+05BE) and sof pasuq (U+05C3).
        text = re.sub(
            r'[\u0591-\u05BD\u05BF\u05C1-\u05C2\u05C4-\u05C5\u05C7]',
            '',
            text,
        )
        text = re.sub(r'([\u05BE\u05C0\u05C3\u05C6\u05F3\u05F4])', r' \1 ', text)

    # Force inline hyphens to be detached so Stanza splits them reliably
    text = separate_inline_hyphens(text)

    if lang == "vie":
        text = separate_vietnamese_sentence_punctuation(text)

    return text


def tokenize_page(text: str, profile: dict, lang: str = ""):
    """Single-page helper retained for controlled tokenizer validation."""
    prepared_text = prepare_page_text(text, profile, lang)
    if not prepared_text.strip():
        return []

    return tokenize_with_stanza(prepared_text, lang)


def tokenize_page_batch(pages: list[str], profile: dict, lang: str = ""):
    """Clean and bulk-tokenize pages without creating cross-page adjacency."""
    prepared_pages = []
    for page in pages:
        prepared_text = prepare_page_text(page, profile, lang)
        if prepared_text.strip():
            prepared_pages.append(prepared_text)

    return tokenize_pages_with_stanza(prepared_pages, lang)


def generate_ngrams_with_counts(words, n):
    """Generate n-grams for local validation and return their counts."""
    if n == 1:
        return Counter((token,) for token in words)

    return Counter(
        tuple(words[index:index + n])
        for index in range(len(words) - n + 1)
    )
