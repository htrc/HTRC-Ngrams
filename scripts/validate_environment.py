#!/usr/bin/env python3
"""Validate the runtime and NLP resources against environment.lock.json."""

import argparse
import hashlib
import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate the HTRC N-grams production environment."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "environment.lock.json",
        help="Path to the environment lock manifest.",
    )
    parser.add_argument(
        "--stanza-resources-dir",
        type=Path,
        default=Path(
            os.environ.get("STANZA_RESOURCES_DIR", Path.home() / "stanza_resources")
        ),
        help="Directory containing Stanza resources.json and model files.",
    )
    return parser.parse_args()


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def file_digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_stopwords_digest():
    from nltk.corpus import stopwords

    file_ids = sorted(stopwords.fileids())
    digest = hashlib.sha256()
    for file_id in file_ids:
        digest.update(file_id.encode("utf-8"))
        digest.update(b"\0")
        for word in stopwords.words(file_id):
            digest.update(word.encode("utf-8"))
            digest.update(b"\n")
    return len(file_ids), digest.hexdigest()


def resolve_stanza_entry(resources, stanza_code):
    resolved_code = stanza_code
    entry = resources.get(resolved_code, {})
    visited = set()
    while isinstance(entry, dict) and "alias" in entry:
        if resolved_code in visited:
            raise RuntimeError(f"Circular Stanza alias detected for {stanza_code}.")
        visited.add(resolved_code)
        resolved_code = entry["alias"]
        entry = resources.get(resolved_code, {})
    return resolved_code, entry


def validate():
    args = parse_args()
    failures = []

    def check(label, actual, expected):
        if actual == expected:
            print(f"[PASS] {label}: {actual}")
        else:
            failures.append(f"{label}: expected {expected!r}, found {actual!r}")
            print(f"[FAIL] {label}: expected {expected!r}, found {actual!r}")

    try:
        manifest = load_json(args.manifest)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[FAIL] Could not read lock manifest {args.manifest}: {error}")
        return 1

    check("Python runtime", platform.python_version(), manifest["python_version"])

    python_version_file = REPO_ROOT / ".python-version"
    try:
        declared_python = python_version_file.read_text(encoding="utf-8").strip()
        check(".python-version", declared_python, manifest["python_version"])
    except OSError as error:
        failures.append(f"Could not read {python_version_file}: {error}")

    for package_name, expected_version in manifest["packages"].items():
        try:
            installed_version = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            installed_version = "not installed"
        check(f"Package {package_name}", installed_version, expected_version)

    try:
        from stanza.resources.common import __resources_version__

        check(
            "Stanza resources release",
            __resources_version__,
            manifest["stanza_resources_release"],
        )
    except (ImportError, AttributeError) as error:
        failures.append(f"Could not determine the Stanza resources release: {error}")
        print("[FAIL] Could not determine the Stanza resources release.")

    try:
        stopword_count, stopword_sha256 = normalized_stopwords_digest()
        stopword_lock = manifest["nltk_resources"]["stopwords"]
        check("NLTK stopword file count", stopword_count, stopword_lock["file_count"])
        check("NLTK stopword content SHA256", stopword_sha256, stopword_lock["sha256"])
    except LookupError as error:
        failures.append(f"NLTK stopwords corpus is unavailable: {error}")
        print("[FAIL] NLTK stopwords corpus is unavailable.")

    resources_path = args.stanza_resources_dir / "resources.json"
    try:
        resources = load_json(resources_path)
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"Could not read {resources_path}: {error}")
        resources = None
        print(f"[FAIL] Could not read Stanza resources manifest: {error}")

    from src.engine_nlp import (
        STANZA_LANG_MAP,
        STANZA_MWT_LANGS,
        SUPPORTED_LANGUAGE_CODES,
    )

    locked_models = manifest["stanza_models"]
    check(
        "Locked Stanza language set",
        sorted(locked_models),
        sorted(SUPPORTED_LANGUAGE_CODES),
    )

    if resources is not None:
        for language in sorted(locked_models):
            locked = locked_models[language]
            stanza_code = STANZA_LANG_MAP.get(language)
            check(f"{language} Stanza code", stanza_code, locked["stanza_code"])

            try:
                resolved_code, entry = resolve_stanza_entry(resources, stanza_code)
            except RuntimeError as error:
                failures.append(str(error))
                continue

            check(
                f"{language} resolved Stanza code",
                resolved_code,
                locked["resolved_stanza_code"],
            )
            default_processors = entry.get("default_processors", {})

            for processor in ("tokenize", "mwt"):
                expected_processor = locked[processor]
                processor_required = (
                    processor == "tokenize" or stanza_code in STANZA_MWT_LANGS
                )
                if not processor_required:
                    check(f"{language} {processor} lock", expected_processor, None)
                    continue
                if expected_processor is None:
                    failures.append(f"{language} is missing its {processor} lock.")
                    print(f"[FAIL] {language} is missing its {processor} lock.")
                    continue

                package = default_processors.get(processor)
                check(
                    f"{language} {processor} package",
                    package,
                    expected_processor["package"],
                )
                resource_md5 = (
                    entry.get(processor, {}).get(package, {}).get("md5", "")
                    if package
                    else ""
                )
                check(
                    f"{language} {processor} resource MD5",
                    resource_md5,
                    expected_processor["md5"],
                )

                model_path = (
                    args.stanza_resources_dir
                    / resolved_code
                    / processor
                    / f"{package}.pt"
                )
                if not model_path.is_file():
                    failures.append(f"Missing Stanza model file: {model_path}")
                    print(f"[FAIL] Missing Stanza model file: {model_path}")
                    continue
                actual_md5 = file_digest(model_path, "md5")
                check(
                    f"{language} {processor} model MD5",
                    actual_md5,
                    expected_processor["md5"],
                )

    if failures:
        print(f"\nEnvironment validation FAILED with {len(failures)} issue(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nEnvironment validation PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate())
