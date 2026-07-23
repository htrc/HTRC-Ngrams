import os
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata


def get_git_commit() -> tuple:
    """Returns (commit_hash, deployment_method)"""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip(), "git"
    except Exception:
        pass
    return "unknown", "scp"


def get_package_version(pkg_name: str) -> str:
    try:
        return importlib_metadata.version(pkg_name)
    except Exception:
        return "unknown"


def get_stanza_model_metadata(stanza_resources_dir: Path) -> dict:
    resources_path = stanza_resources_dir / "resources.json"
    if not resources_path.exists():
        return {
            "model_provenance_status": "unavailable",
            "model_provenance_error": f"resources.json not found at {resources_path}",
        }

    try:
        with open(resources_path, "r", encoding="utf-8") as f:
            resources_data = json.load(f)

        from src.engine_nlp import STANZA_LANG_MAP, STANZA_MWT_LANGS

        models_meta = {}
        for marc_code, stanza_code in STANZA_LANG_MAP.items():
            if marc_code in ("ger", "fre", "chi", "ces"):
                continue
            lang_entry = resources_data.get(stanza_code, {})
            default_procs = lang_entry.get("default_processors", {})

            # Tokenizer
            tok_pkg = default_procs.get("tokenize", "")
            tok_dict = lang_entry.get("tokenize", {})
            tok_md5 = tok_dict.get(tok_pkg, {}).get("md5", "") if tok_pkg else ""

            # MWT
            mwt_pkg = ""
            mwt_md5 = ""
            if stanza_code in STANZA_MWT_LANGS:
                mwt_pkg = default_procs.get("mwt", "")
                mwt_dict = lang_entry.get("mwt", {})
                mwt_md5 = mwt_dict.get(mwt_pkg, {}).get("md5", "") if mwt_pkg else ""

            models_meta[marc_code] = {
                "stanza_code": stanza_code,
                "tokenizer_package": tok_pkg,
                "tokenizer_md5": tok_md5,
                "mwt_package": mwt_pkg,
                "mwt_md5": mwt_md5,
            }

        return {
            "model_provenance_status": "available",
            "models": models_meta,
        }
    except Exception as e:
        return {
            "model_provenance_status": "unavailable",
            "model_provenance_error": f"Failed to parse resources.json: {e}",
        }


def generate_run_provenance(
    run_id: str,
    enable_f1: bool,
    enable_f2: bool,
    enable_f3: bool,
    min_freq: int,
    min_pmi: float,
    stanza_resources_dir: Path,
) -> dict:
    git_commit, deployment_method = get_git_commit()
    return {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "deployment_method": deployment_method,
        "python_version": sys.version.split()[0],
        "stanza_version": get_package_version("stanza"),
        "nltk_version": get_package_version("nltk"),
        "stopwordsiso_version": get_package_version("stopwordsiso"),
        "langid_version": get_package_version("langid"),
        "filters": {
            "filter_1": enable_f1,
            "filter_2": enable_f2,
            "filter_3": enable_f3,
            "min_frequency": min_freq,
            "min_pmi": min_pmi,
        },
        "stanza_models": get_stanza_model_metadata(stanza_resources_dir),
    }


def write_run_provenance_once(provenance_data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = provenance_data["run_id"]
    file_path = output_dir / f"{run_id}.json"

    try:
        fd = os.open(str(file_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(provenance_data, f, indent=2)
    except FileExistsError:
        pass

    return file_path
