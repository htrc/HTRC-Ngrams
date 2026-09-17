"""
Configuration constraints and static dictionaries.
"""

LANGUAGE_PROFILES = {

    # --- Latin Script Languages ---

    "eng": {
        "header_keywords": ["CHAPTER", "BOOK", "VOL", "VOLUME", "PAGE", "PART", "SECTION"],
        "script": "latin"
    },
    "fra": {
        "header_keywords": ["CHAPITRE", "LIVRE", "VOLUME", "PAGE", "TOME", "PARTIE"],
        "script": "latin"
    },
    "spa": {
        "header_keywords": ["CAPITULO", "LIBRO", "VOLUMEN", "PAGINA", "PARTE"],
        "script": "latin"
    },
    "deu": {
        "header_keywords": ["KAPITEL", "BAND", "SEITE", "TEIL", "BUCH", "ABSCHNITT", "INHALT", "VORWORT", "ANHANG", "REGISTER"],
        "script": "latin"
    },
    "ita": {
        "header_keywords": ["CAPITOLO", "LIBRO", "VOLUME", "PAGINA", "PARTE", "SEZIONE"],
        "script": "latin"
    },
    "por": {
        "header_keywords": ["CAPITULO", "LIVRO", "VOLUME", "PAGINA", "PARTE", "SECCAO"],
        "script": "latin"
    },
    "lat": {
        "header_keywords": ["CAPUT", "LIBER", "TOMUS", "PARS", "PAGINA", "VOLUMEN", "SECTIO"],
        "script": "latin"
    },
    "dut": {
        "header_keywords": ["HOOFDSTUK", "BOEK", "DEEL", "PAGINA", "BLADZIJDE", "STUK", "AFDELING", "BAND"],
        "script": "latin"
    },
    "pol": {
        "header_keywords": ["ROZDZIAŁ", "KSIĘGA", "TOM", "STRONA", "CZĘŚĆ", "DZIAŁ", "USTĘP"],
        "script": "latin"
    },
    "swe": {
        "header_keywords": ["KAPITEL", "BOK", "DEL", "SIDA", "BAND", "AVDELNING", "STYCKE"],
        "script": "latin"
    },

    # --- Cyrillic Script Languages ---

    "rus": {
        "header_keywords": ["ГЛАВА", "КНИГА", "ТОМ", "ЧАСТЬ", "РАЗДЕЛ"],
        "script": "cyrillic"
    },

    # --- CJK Script Languages ---

    "zho": {
        "header_keywords": [],
        "script": "cjk"
    },
    "jpn": {
        "header_keywords": [],
        "script": "cjk"
    },

    # --- Arabic Script Languages ---

    "ara": {
        "header_keywords": [],
        "script": "arabic"
    },

    # --- Hebrew Script Languages ---

    "heb": {
        "header_keywords": ["פרק", "ספר", "חלק", "עמוד", "כרך", "שער", "סימן"],
        "script": "hebrew"
    },

    # --- Korean and Additional Latin-Script Languages ---

    "kor": {
        "header_keywords": ["목차", "차례", "서문", "머리말", "부록", "색인", "참고문헌", "目次", "序文", "附錄", "索引"],
        "script": "korean"
    },
    "cze": {
        "header_keywords": [
            "KAPITOLA", "KNIHA", "DÍL", "DIL", "STRANA",
            "ČÁST", "CAST", "OBSAH", "PŘEDMLUVA", "PREDMLUVA",
            "ÚVOD", "UVOD", "REJSTŘÍK", "REJSTRIK"
        ],
        "script": "latin"
    },
    "dan": {
        "header_keywords": [
            "KAPITEL", "BOG", "BIND", "SIDE", "DEL",
            "AFSNIT", "INDHOLD", "FORORD", "TILLÆG", "TILLAEG", "REGISTER"
        ],
        "script": "latin"
    },
    "ind": {
        "header_keywords": [
            "BAB", "BUKU", "JILID", "HALAMAN", "BAGIAN",
            "DAFTAR", "ISI", "PENDAHULUAN", "PENGANTAR", "LAMPIRAN"
        ],
        "script": "latin"
    },
    "hun": {
        "header_keywords": [
            "FEJEZET", "KÖNYV", "KONYV", "KÖTET", "KOTET",
            "OLDAL", "RÉSZ", "RESZ", "TARTALOM",
            "ELŐSZÓ", "ELOSZO", "FÜGGELÉK", "FUGGELEK", "MUTATÓ", "MUTATO"
        ],
        "script": "latin"
    },

    # --- Additional HTRC Languages ---

    "hin": {
        "header_keywords": [
            "अध्याय", "पुस्तक", "खंड", "पृष्ठ", "भाग",
            "विषय सूची", "अनुक्रमणिका", "प्रस्तावना", "परिशिष्ट"
        ],
        "script": "devanagari"
    },
    "hrv": {
        "header_keywords": [
            "POGLAVLJE", "KNJIGA", "SVEZAK", "STRANICA", "DIO",
            "SADRŽAJ", "PREDGOVOR", "DODATAK", "KAZALO"
        ],
        "script": "latin"
    },
    "tur": {
        "header_keywords": [
            "BÖLÜM", "KİTAP", "CİLT", "SAYFA", "KISIM",
            "İÇİNDEKİLER", "ÖNSÖZ", "EK", "DİZİN"
        ],
        "script": "latin"
    },
    "nor": {
        "header_keywords": [
            "KAPITTEL", "BOK", "BIND", "SIDE", "DEL", "INNHOLD", "INNHALD",
            "FORORD", "FØREORD", "VEDLEGG", "REGISTER"
        ],
        "script": "latin"
    },
    "tha": {
        "header_keywords": [
            "บท", "หนังสือ", "เล่ม", "หน้า", "ส่วน",
            "สารบัญ", "คำนำ", "ภาคผนวก", "ดัชนี"
        ],
        "script": "thai"
    },
    "vie": {
        "header_keywords": [
            "CHƯƠNG", "SÁCH", "TẬP", "TRANG", "PHẦN", "MỤC LỤC",
            "LỜI NÓI ĐẦU", "PHỤ LỤC", "CHỈ MỤC"
        ],
        "script": "latin"
    },
    "urd": {
        "header_keywords": [
            "باب", "کتاب", "جلد", "صفحہ", "حصہ", "فہرست",
            "دیباچہ", "ضمیمہ", "اشاریہ"
        ],
        "script": "arabic"
    }
}
