"""
Configuration constraints and static dictionaries.
"""

LANGUAGE_PROFILES = {
    "eng": {
        "header_keywords": ["CHAPTER", "BOOK", "VOL", "VOLUME", "PAGE", "PART", "SECTION"],
        "allowed_single_chars": ["a", "i", "o"], # o for poetic O
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
