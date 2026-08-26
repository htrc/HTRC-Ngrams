"""
Configuration constraints and static dictionaries.
"""

LANGUAGE_PROFILES = {

    # --- Latin Script Languages ---

    "eng": {
        "header_keywords": ["CHAPTER", "BOOK", "VOL", "VOLUME", "PAGE", "PART", "SECTION"],
        "allowed_single_chars": ["a", "i"],
        "script": "latin"
    },
    "fra": {
        "header_keywords": ["CHAPITRE", "LIVRE", "VOLUME", "PAGE", "TOME", "PARTIE"],
        "allowed_single_chars": ["a", "y", "o", "à", "ô", "l", "c", "d", "j", "m", "n", "s"],
        "script": "latin"
    },
    "spa": {
        "header_keywords": ["CAPITULO", "LIBRO", "VOLUMEN", "PAGINA", "PARTE"],
        "allowed_single_chars": ["a", "y", "o", "e", "u"],
        "script": "latin"
    },
    "deu": {
        "header_keywords": ["KAPITEL", "BAND", "SEITE", "TEIL", "BUCH", "ABSCHNITT", "INHALT", "VORWORT", "ANHANG", "REGISTER"],
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
    "lat": {
        "header_keywords": ["CAPUT", "LIBER", "TOMUS", "PARS", "PAGINA", "VOLUMEN", "SECTIO"],
        "allowed_single_chars": ["a", "e", "i", "o", "u", "v", "x", "l", "c", "d", "m"],
        "script": "latin"
    },
    "dut": {
        "header_keywords": ["HOOFDSTUK", "BOEK", "DEEL", "PAGINA", "BLADZIJDE", "STUK", "AFDELING", "BAND"],
        "allowed_single_chars": ["a", "u", "o", "s", "t", "k", "m", "n", "d"],
        "script": "latin"
    },
    "pol": {
        "header_keywords": ["ROZDZIAŁ", "KSIĘGA", "TOM", "STRONA", "CZĘŚĆ", "DZIAŁ", "USTĘP"],
        "allowed_single_chars": ["a", "i", "o", "u", "w", "z"],
        "script": "latin"
    },
    "swe": {
        "header_keywords": ["KAPITEL", "BOK", "DEL", "SIDA", "BAND", "AVDELNING", "STYCKE"],
        "allowed_single_chars": ["i", "å", "ö", "o", "e", "a"],
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

    # --- Hebrew Script Languages ---

    "heb": {
        "header_keywords": ["פרק", "ספר", "חלק", "עמוד", "כרך", "שער", "סימן"],
        "allowed_single_chars": ["ב", "כ", "ל", "מ", "ה", "ו", "ש"],
        "script": "hebrew"
    },

    # --- New 5 Languages (Korean, Czech, Danish, Indonesian, Hungarian) ---

    "kor": {
        "header_keywords": ["목차", "차례", "서문", "머리말", "부록", "색인", "참고문헌", "目次", "序文", "附錄", "索引"],
        "allowed_single_chars": [],
        "script": "korean"
    },
    "cze": {
        "header_keywords": [
            "KAPITOLA", "KNIHA", "DÍL", "DIL", "STRANA",
            "ČÁST", "CAST", "OBSAH", "PŘEDMLUVA", "PREDMLUVA",
            "ÚVOD", "UVOD", "REJSTŘÍK", "REJSTRIK"
        ],
        "allowed_single_chars": ["a", "i", "o", "u", "v", "z", "k", "s", "ó"],
        "script": "latin"
    },
    "dan": {
        "header_keywords": [
            "KAPITEL", "BOG", "BIND", "SIDE", "DEL",
            "AFSNIT", "INDHOLD", "FORORD", "TILLÆG", "TILLAEG", "REGISTER"
        ],
        "allowed_single_chars": ["i", "a", "o", "å", "ø"],
        "script": "latin"
    },
    "ind": {
        "header_keywords": [
            "BAB", "BUKU", "JILID", "HALAMAN", "BAGIAN",
            "DAFTAR", "ISI", "PENDAHULUAN", "PENGANTAR", "LAMPIRAN"
        ],
        "allowed_single_chars": ["o"],
        "script": "latin"
    },
    "hun": {
        "header_keywords": [
            "FEJEZET", "KÖNYV", "KONYV", "KÖTET", "KOTET",
            "OLDAL", "RÉSZ", "RESZ", "TARTALOM",
            "ELŐSZÓ", "ELOSZO", "FÜGGELÉK", "FUGGELEK", "MUTATÓ", "MUTATO"
        ],
        "allowed_single_chars": ["a", "s", "e", "ő", "ó"],
        "script": "latin"
    },

    # --- Additional HTRC Languages ---

    "hin": {
        "header_keywords": [
            "अध्याय", "पुस्तक", "खंड", "पृष्ठ", "भाग",
            "विषय सूची", "अनुक्रमणिका", "प्रस्तावना", "परिशिष्ट"
        ],
        "allowed_single_chars": [],
        "script": "devanagari"
    },
    "hrv": {
        "header_keywords": [
            "POGLAVLJE", "KNJIGA", "SVEZAK", "STRANICA", "DIO",
            "SADRŽAJ", "PREDGOVOR", "DODATAK", "KAZALO"
        ],
        "allowed_single_chars": ["a", "i", "u", "o", "s", "k"],
        "script": "latin"
    },
    "tur": {
        "header_keywords": [
            "BÖLÜM", "KİTAP", "CİLT", "SAYFA", "KISIM",
            "İÇİNDEKİLER", "ÖNSÖZ", "EK", "DİZİN"
        ],
        "allowed_single_chars": ["o"],
        "script": "latin"
    },
    "nor": {
        "header_keywords": [
            "KAPITTEL", "BOK", "BIND", "SIDE", "DEL", "INNHOLD", "INNHALD",
            "FORORD", "FØREORD", "VEDLEGG", "REGISTER"
        ],
        "allowed_single_chars": ["i", "å"],
        "script": "latin"
    },
    "tha": {
        "header_keywords": [
            "บท", "หนังสือ", "เล่ม", "หน้า", "ส่วน",
            "สารบัญ", "คำนำ", "ภาคผนวก", "ดัชนี"
        ],
        "allowed_single_chars": [],
        "script": "thai"
    },
    "vie": {
        "header_keywords": [
            "CHƯƠNG", "SÁCH", "TẬP", "TRANG", "PHẦN", "MỤC LỤC",
            "LỜI NÓI ĐẦU", "PHỤ LỤC", "CHỈ MỤC"
        ],
        "allowed_single_chars": ["a", "à", "ạ", "ở"],
        "script": "latin"
    },
    "urd": {
        "header_keywords": [
            "باب", "کتاب", "جلد", "صفحہ", "حصہ", "فہرست",
            "دیباچہ", "ضمیمہ", "اشاریہ"
        ],
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
