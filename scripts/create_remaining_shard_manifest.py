#!/usr/bin/env python3
"""Build a new shard manifest from the unfinished suffix of a campaign."""

import argparse
import csv
import json
import os
import uuid
from pathlib import Path


def normalize_path(value: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(value)))


def atomic_write_lines(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as file:
            file.writelines(lines)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-manifest", required=True, type=Path)
    parser.add_argument("--start-wave", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    with args.campaign_manifest.open(encoding="utf-8") as file:
        campaign = json.load(file)

    wave_count = int(campaign["wave_count"])
    if not 0 <= args.start_wave < wave_count:
        raise SystemExit(f"start-wave must be between 0 and {wave_count - 1}")

    manifest_output = args.output_dir / "shards_manifest.txt"
    counts_output = args.output_dir / "shard_counts.tsv"
    if not args.overwrite and (manifest_output.exists() or counts_output.exists()):
        raise SystemExit(f"Output already exists in {args.output_dir}")

    shard_paths = []
    for wave in campaign["waves"][args.start_wave:]:
        wave_manifest = Path(wave["manifest"])
        with wave_manifest.open(encoding="utf-8") as file:
            shard_paths.extend(
                normalize_path(line.strip())
                for line in file
                if line.strip()
            )
    print(f"Loaded {len(shard_paths):,} remaining shard path(s).", flush=True)

    source_counts = Path(campaign["source_shard_counts"])
    wanted = set(shard_paths)
    counts = {}
    with source_counts.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for row in reader:
            path = normalize_path(row["path"])
            if path in wanted:
                counts[path] = int(row["record_count"])

    missing = wanted - counts.keys()
    if missing:
        raise RuntimeError(f"Missing record count for shard: {next(iter(missing))}")

    atomic_write_lines(
        manifest_output,
        (f"{path}\n" for path in shard_paths),
    )
    atomic_write_lines(
        counts_output,
        ["path\trecord_count\n"] + [
            f"{path}\t{counts[path]}\n"
            for path in shard_paths
        ],
    )

    print(f"Starting original wave: {args.start_wave}")
    print(f"Remaining shards: {len(shard_paths):,}")
    print(f"Remaining records: {sum(counts.values()):,}")
    print(f"Shard manifest: {manifest_output.resolve()}")
    print(f"Shard counts: {counts_output.resolve()}")


if __name__ == "__main__":
    main()
