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
            resolved_stanza_code = stanza_code
            visited_aliases = set()
            while "alias" in lang_entry:
                if resolved_stanza_code in visited_aliases:
                    break
                visited_aliases.add(resolved_stanza_code)
                resolved_stanza_code = lang_entry["alias"]
                lang_entry = resources_data.get(resolved_stanza_code, {})
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
                "resolved_stanza_code": resolved_stanza_code,
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
    stanza_resources_dir: Path,
    output_format: str = "tsv.gz",
    gzip_level: int = 1,
) -> dict:
    git_commit, deployment_method = get_git_commit()
    return {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "deployment_method": deployment_method,
        "python_version": sys.version.split()[0],
        "stanza_version": get_package_version("stanza"),
        "langid_version": get_package_version("langid"),
        "export_policy": "unfiltered",
        "output_format": output_format,
        "gzip_level": gzip_level,
        "execution_resources": {
            "slurm_cpus_per_task": int(
                os.environ.get("SLURM_CPUS_PER_TASK", "1")
            ),
            "slurm_mem_per_node_mb": (
                int(os.environ["SLURM_MEM_PER_NODE"])
                if os.environ.get("SLURM_MEM_PER_NODE", "").isdigit()
                else None
            ),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "mkl_num_threads": os.environ.get("MKL_NUM_THREADS"),
        },
        "stanza_models": get_stanza_model_metadata(stanza_resources_dir),
    }


def validate_existing_provenance(file_path, expected_data):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            existing = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Invalid provenance file: {file_path}"
        ) from error

    if not isinstance(existing, dict):
        raise RuntimeError("Provenance must contain a JSON object.")

    ignored_fields = {"created_at_utc"}

    expected_stable = {
        key: value
        for key, value in expected_data.items()
        if key not in ignored_fields
    }

    existing_stable = {
        key: value
        for key, value in existing.items()
        if key not in ignored_fields
    }

    if existing_stable != expected_stable:
        raise RuntimeError(
            f"Existing provenance does not match this run: {file_path}"
        )

def write_run_provenance_once(provenance_data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = provenance_data["run_id"]
    file_path = output_dir / f"{run_id}.json"

    if file_path.exists():
        validate_existing_provenance(file_path, provenance_data)
        return file_path

    import uuid
    pid = os.getpid()
    uid = uuid.uuid4().hex[:8]
    tmp_path = file_path.with_name(f"{file_path.name}.{pid}_{uid}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(provenance_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp_path, file_path)
        except FileExistsError:
            validate_existing_provenance(file_path, provenance_data)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return file_path
