#!/usr/bin/env python3
"""Create language-homogeneous retry shards from a campaign pending report."""

import argparse
import csv
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine_nlp import canonicalize_language, is_supported_language


PENDING_FIELDS = ["htid", "reason_code", "detail"]


def create_retry_shards(
    pending_file: Path,
    source_manifest: Path,
    output_dir: Path,
    target_volumes: int,
    reason_codes: set,
    overwrite: bool,
) -> Path:
    with open(pending_file, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        if reader.fieldnames != PENDING_FIELDS:
            raise RuntimeError(f"Unexpected pending header: {reader.fieldnames}")
        pending_ids = {
            row["htid"]
            for row in reader
            if row.get("htid")
            and (not reason_codes or row.get("reason_code") in reason_codes)
        }
    if not pending_ids:
        raise RuntimeError("No pending HTIDs matched the requested reason codes")

    shard_paths = [
        Path(line.strip())
        for line in source_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records_by_group = defaultdict(list)
    matched = set()
    for shard_path in shard_paths:
        with open(shard_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, delimiter="\t")
            required = {"htid", "language", "zip_size_bytes"}
            if not required.issubset(reader.fieldnames or []):
                raise RuntimeError(f"Invalid source shard header in {shard_path}")
            for row in reader:
                htid = (row.get("htid") or "").strip()
                if htid not in pending_ids:
                    continue
                language = canonicalize_language(row.get("language") or "")
                group = language if is_supported_language(language) else "unsupported"
                records_by_group[group].append((
                    htid,
                    language or "unknown",
                    row.get("zip_size_bytes") or "",
                ))
                matched.add(htid)

    missing = pending_ids - matched
    if missing:
        raise RuntimeError(
            f"Could not recover {len(missing):,} pending HTIDs from source shards; "
            f"example: {sorted(missing)[0]}"
        )

    if output_dir.exists():
        if not overwrite:
            raise RuntimeError(
                f"Retry shard directory already exists: {output_dir}"
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    manifest_paths = []
    for group in sorted(records_by_group):
        records = records_by_group[group]
        shard_count = math.ceil(len(records) / target_volumes)
        for shard_index in range(shard_count):
            start = shard_index * target_volumes
            shard_records = records[start:start + target_volumes]
            shard_path = output_dir / f"{group}_{shard_index:04d}.tsv"
            with open(shard_path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="\t", lineterminator="\n")
                writer.writerow(["htid", "language", "zip_size_bytes"])
                writer.writerows(shard_records)
            manifest_paths.append(shard_path.resolve())

    manifest_path = output_dir / "shards_manifest.txt"
    with open(manifest_path, "w", encoding="utf-8") as file:
        for path in manifest_paths:
            file.write(f"{path}\n")

    print(f"Retry volumes: {len(matched):,}")
    print(f"Retry shards: {len(manifest_paths):,}")
    print(f"Retry manifest: {manifest_path.resolve()}")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending-file", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--target-volumes", type=int, default=100)
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.target_volumes <= 0:
        parser.error("--target-volumes must be greater than zero")
    create_retry_shards(
        args.pending_file,
        args.source_manifest,
        args.output_dir,
        args.target_volumes,
        set(args.reason_code),
        args.overwrite,
    )


if __name__ == "__main__":
    main()
