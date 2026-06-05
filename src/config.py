"""
Configuration constraints and static dictionaries.
"""

LANGUAGE_PROFILES = {

    # --- Latin Script Languages ---

    "eng": {
        "header_keywords": ["CHAPTER", "BOOK", "VOL", "VOLUME", "PAGE", "PART", "SECTION"],
        "allowed_single_chars": ["a", "i", "o"],  # 'o' for poetic usage
        "script": "latin"
    },
    "fra": {
        "header_keywords": ["CHAPITRE", "LIVRE", "VOLUME", "PAGE", "TOME", "PARTIE"],
        "allowed_single_chars": ["a", "y", "o", "à", "ô"],
        "script": "latin"
    },
    "spa": {
        "header_keywords": ["CAPITULO", "LIBRO", "VOLUMEN", "PAGINA", "PARTE"],
        "allowed_single_chars": ["a", "y", "o", "e", "u"],
        "script": "latin"
    },
    "deu": {
        "header_keywords": ["KAPITEL", "BAND", "SEITE", "TEIL"],
        "allowed_single_chars": [],
        "script": "latin"
    },
    "ita": {
        "header_keywords": ["CAPITOLO", "LIBRO", "VOLUME", "PAGINA", "PARTE", "SEZIONE"],
        "allowed_single_chars": ["a", "e", "o", "i", "u"],  # Italian uses single-vowel words frequently
        "script": "latin"
    },
    "por": {
        "header_keywords": ["CAPITULO", "LIVRO", "VOLUME", "PAGINA", "PARTE", "SECCAO"],
        "allowed_single_chars": ["a", "e", "o"],  # Portuguese: 'a' (the/her), 'e' (and), 'o' (the/him)
        "script": "latin"
    },

    # --- Cyrillic Script Languages ---

    "rus": {
        # Common Russian structural headers in Cyrillic
        "header_keywords": ["ГЛАВА", "КНИГА", "ТОМ", "ЧАСТЬ", "РАЗДЕЛ"],
        # Common single-character Russian prepositions and conjunctions
        "allowed_single_chars": ["в", "с", "к", "у", "и", "я", "а", "о"],
        "script": "cyrillic"
    },

    # --- CJK Script Languages (safe bypass — no Latin tokenizer applied) ---

    "zho": {
        "header_keywords": [],
        "allowed_single_chars": [],
        "script": "cjk"
    },
    "jpn": {
        "header_keywords": [],
        "allowed_single_chars": [],
        "script": "cjk"
    },

    # --- Arabic Script Languages (safe bypass — RTL, complex ligature system) ---

    "ara": {
        "header_keywords": [],
        "allowed_single_chars": [],
        "script": "arabic"
    },

    # --- Fallbacks ---

    "default_latin": {
        "header_keywords": [],
        "allowed_single_chars": [],
        "script": "latin"
    },
    "cjk": {
        "header_keywords": [],
        "allowed_single_chars": [],
        "script": "cjk"
    }
}
