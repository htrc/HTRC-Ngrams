#!/usr/bin/env python3
"""Validate the HTRC Stanza tokenization path for all supported languages."""

import argparse
import csv
import gc
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import stanza
from stanza.resources.common import DEFAULT_RESOURCES_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import engine_nlp
from src.config import LANGUAGE_PROFILES


@dataclass(frozen=True)
class ValidationCase:
    marc_code: str
    language: str
    text: str
    has_case: bool = True
    expected_tokens: tuple = ()
    expected_sequences: tuple = ()


VALIDATION_CASES = (
    ValidationCase(
        "eng",
        "English",
        "The well-known library doesn't support inter-\nnational research with only 2 books.",
        expected_tokens=("international",),
        expected_sequences=(
            ("well", "-", "known"),
            ("does", "n't"),
        ),
    ),
    ValidationCase("deu", "German", "Die Bibliothek hat 2 Bücher."),
    ValidationCase(
        "fra",
        "French",
        "L'arbre près de la bibliothèque possède 2 livres.",
        expected_sequences=(
            ("L'", "arbre"),
        ),
    ),
    ValidationCase("spa", "Spanish", "La biblioteca tiene 2 libros."),
    ValidationCase("zho", "Chinese", "图书馆有2本书.", has_case=False),
    ValidationCase("jpn", "Japanese", "図書館には本が2冊あります.", has_case=False),
    ValidationCase("rus", "Russian", "В библиотеке есть 2 книги."),
    ValidationCase("ita", "Italian", "La biblioteca ha 2 libri."),
    ValidationCase("por", "Portuguese", "A biblioteca tem 2 livros."),
    ValidationCase("ara", "Arabic", "تحتوي المكتبة على 2 كتاب.", has_case=False),
    ValidationCase("lat", "Latin", "Bibliotheca 2 libros habet."),
    ValidationCase("dut", "Dutch", "De bibliotheek heeft 2 boeken."),
    ValidationCase("pol", "Polish", "Biblioteka ma 2 książki."),
    ValidationCase("swe", "Swedish", "Biblioteket har 2 böcker."),
    ValidationCase("heb", "Hebrew", "בספרייה יש 2 ספרים.", has_case=False),
    ValidationCase("kor", "Korean", "도서관에는 책이 2권 있습니다.", has_case=False),
    ValidationCase("cze", "Czech", "Knihovna má 2 knihy."),
    ValidationCase("dan", "Danish", "Biblioteket har 2 bøger."),
    ValidationCase("ind", "Indonesian", "Perpustakaan memiliki 2 buku."),
    ValidationCase("hun", "Hungarian", "A könyvtárban 2 könyv van."),
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Initialize each configured Stanza tokenizer and verify the HTRC "
            "pipeline preserves tokens, case, numbers, and punctuation."
        )
    )
    parser.add_argument(
        "--resource-dir",
        type=Path,
        help="Stanza model directory. Defaults to STANZA_RESOURCES_DIR or ~/stanza_resources.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "test_outputs" / "stanza_20_language_validation.tsv",
        help="TSV report path.",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        metavar="MARC_CODE",
        help="Optional subset of MARC codes to validate.",
    )
    return parser.parse_args()


def get_cases(requested_languages):
    cases_by_code = {case.marc_code: case for case in VALIDATION_CASES}
    if not requested_languages:
        return list(VALIDATION_CASES)

    unknown = sorted(set(requested_languages) - set(cases_by_code))
    if unknown:
        raise ValueError(f"Unsupported validation language codes: {', '.join(unknown)}")

    return [cases_by_code[code] for code in requested_languages]


def validate_case(case):
    started_at = time.perf_counter()
    stanza_code = engine_nlp.STANZA_LANG_MAP.get(case.marc_code)
    expected_processors = {"tokenize"}
    tokens = []
    failures = []
    warnings = []
    tok_pkg = ""
    tok_md5 = ""
    mwt_pkg = ""
    mwt_md5 = ""

    if stanza_code is None:
        failures.append("missing STANZA_LANG_MAP entry")
    elif stanza_code in engine_nlp.STANZA_MWT_LANGS:
        expected_processors.add("mwt")

    profile = LANGUAGE_PROFILES.get(case.marc_code)
    if profile is None:
        failures.append("missing LANGUAGE_PROFILES entry")

    actual_processors = set()
    if not failures:
        try:
            pipeline = engine_nlp.get_stanza_pipeline(case.marc_code)
            actual_processors = set(pipeline.processors)
            missing_processors = expected_processors - actual_processors
            if missing_processors:
                failures.append(
                    f"missing processors: {', '.join(sorted(missing_processors))}"
                )
                
            # Extract provenance
            if "tokenize" in actual_processors:
                tok_path = pipeline.processors["tokenize"].config.get("model_path", "")
                tok_pkg = Path(tok_path).stem if tok_path else ""
                
            if "mwt" in actual_processors:
                mwt_path = pipeline.processors["mwt"].config.get("model_path", "")
                mwt_pkg = Path(mwt_path).stem if mwt_path else ""

            tokens = engine_nlp.tokenize_page(case.text, profile, case.marc_code)
            if not tokens:
                failures.append("tokenizer returned no tokens")
            if not any("2" in token for token in tokens):
                failures.append("numeric content was not preserved")
            elif "2" not in tokens:
                warnings.append("numeric content was preserved inside a combined token")
            if "." not in tokens:
                failures.append("sentence punctuation was not preserved")
            if case.has_case and not any(
                any(character.isupper() for character in token) for token in tokens
            ):
                failures.append("capitalization was not preserved")

            for expected_token in case.expected_tokens:
                if expected_token not in tokens:
                    failures.append(f"missing expected token: {expected_token}")

            for expected_sequence in case.expected_sequences:
                if not contains_sequence(tokens, list(expected_sequence)):
                    failures.append(
                        f"missing expected token sequence: {' '.join(expected_sequence)}"
                    )
        except Exception as error:
            failures.append(f"{type(error).__name__}: {error}")

    elapsed_seconds = time.perf_counter() - started_at
    result = {
        "marc_code": case.marc_code,
        "language": case.language,
        "stanza_code": stanza_code or "",
        "expected_processors": ",".join(sorted(expected_processors)),
        "actual_processors": ",".join(sorted(actual_processors)),
        "tok_pkg": tok_pkg,
        "mwt_pkg": mwt_pkg,
        "status": "PASS" if not failures else "FAIL",
        "token_count": len(tokens),
        "elapsed_seconds": f"{elapsed_seconds:.3f}",
        "tokens": json.dumps(tokens, ensure_ascii=False),
        "warnings": "; ".join(warnings),
        "details": "; ".join(failures),
    }

    engine_nlp._STANZA_PIPELINES.clear()
    gc.collect()
    return result


def contains_sequence(tokens, expected_sequence):
    sequence_length = len(expected_sequence)
    return any(
        tokens[index:index + sequence_length] == expected_sequence
        for index in range(len(tokens) - sequence_length + 1)
    )


def write_report(results, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=results[0].keys(), delimiter="\t")
        writer.writeheader()
        writer.writerows(results)


def main():
    args = parse_args()
    resource_dir = args.resource_dir or Path(
        os.environ.get("STANZA_RESOURCES_DIR", Path.home() / "stanza_resources")
    )
    os.environ["STANZA_RESOURCES_DIR"] = str(resource_dir)

    try:
        cases = get_cases(args.languages)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    results = []
    
    # Load resources.json for MD5 lookups
    resources_path = resource_dir / "resources.json"
    resources_data = {}
    if resources_path.exists():
        with open(resources_path, "r", encoding="utf-8") as f:
            resources_data = json.load(f)

    for case in cases:
        print(f"Validating {case.language} ({case.marc_code})...", flush=True)
        result = validate_case(case)
        
        # Append MD5 and Stanza versions
        sc = result["stanza_code"]
        result["stanza_version"] = stanza.__version__
        result["stanza_resources_version"] = DEFAULT_RESOURCES_VERSION
        
        tok_md5 = resources_data.get(sc, {}).get("tokenize", {}).get(result["tok_pkg"], {}).get("md5", "")
        mwt_md5 = resources_data.get(sc, {}).get("mwt", {}).get(result["mwt_pkg"], {}).get("md5", "")
        
        result["tok_md5"] = tok_md5
        result["mwt_md5"] = mwt_md5
        
        results.append(result)
        print(
            f"  {result['status']}: {result['token_count']} tokens "
            f"in {result['elapsed_seconds']} seconds"
        )
        if result["details"]:
            print(f"  {result['details']}")
        if result["warnings"]:
            print(f"  WARNING: {result['warnings']}")

    write_report(results, args.output)
    passed = sum(result["status"] == "PASS" for result in results)
    failed = len(results) - passed
    print(f"\nValidation result: {passed} passed, {failed} failed")
    print(f"Report: {args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
