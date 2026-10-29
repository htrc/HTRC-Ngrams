#!/usr/bin/env python3
"""Split a task-shard manifest into bounded production submission waves."""

import argparse
import csv
import json
import shutil
from pathlib import Path


def count_shard_records(path: Path) -> int:
    with open(path, "r", encoding="utf-8") as file:
        return max(sum(1 for _ in file) - 1, 0)


def load_shard_counts(path: Path) -> dict[Path, int]:
    counts = {}
    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        required = {"path", "record_count"}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(f"Invalid shard-count header in {path}")
        for row in reader:
            shard_path = Path(row["path"]).resolve()
            if shard_path in counts:
                raise RuntimeError(
                    f"Duplicate shard path in {path}: {shard_path}"
                )
            try:
                record_count = int(row["record_count"])
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f"Invalid record count for {shard_path}: "
                    f"{row['record_count']!r}"
                ) from error
            if record_count <= 0:
                raise RuntimeError(
                    f"Non-positive record count for {shard_path}: {record_count}"
                )
            counts[shard_path] = record_count
    return counts


def create_wave_manifests(
    shard_manifest: Path,
    shard_counts_path: Path,
    output_dir: Path,
    wave_size: int,
    overwrite: bool,
) -> Path:
    shard_paths = [
        Path(line.strip()).resolve()
        for line in shard_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not shard_paths:
        raise RuntimeError(f"No shard paths found in {shard_manifest}")
    if len(set(shard_paths)) != len(shard_paths):
        raise RuntimeError(f"Duplicate shard paths found in {shard_manifest}")

    missing = [str(path) for path in shard_paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing shard file: {missing[0]}")

    if shard_counts_path is not None:
        shard_record_counts = load_shard_counts(shard_counts_path)
        manifest_paths = set(shard_paths)
        count_paths = set(shard_record_counts)
        if manifest_paths != count_paths:
            missing_count = manifest_paths - count_paths
            extra_count = count_paths - manifest_paths
            detail = []
            if missing_count:
                detail.append(f"missing count for {next(iter(missing_count))}")
            if extra_count:
                detail.append(f"unexpected count for {next(iter(extra_count))}")
            raise RuntimeError(
                f"Shard manifest/count mismatch: {'; '.join(detail)}"
            )
    else:
        shard_record_counts = {
            path: count_shard_records(path)
            for path in shard_paths
        }

    if output_dir.exists():
        if not overwrite:
            raise RuntimeError(
                f"Wave directory already exists: {output_dir}. "
                "Use --overwrite only for a new campaign."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    waves = []
    for wave_index, start in enumerate(range(0, len(shard_paths), wave_size)):
        paths = shard_paths[start:start + wave_size]
        wave_path = output_dir / f"wave_{wave_index:04d}.manifest"
        with open(wave_path, "w", encoding="utf-8") as file:
            for path in paths:
                file.write(f"{path}\n")
        waves.append({
            "wave_index": wave_index,
            "manifest": str(wave_path.resolve()),
            "task_count": len(paths),
            "record_count": sum(shard_record_counts[path] for path in paths),
        })

    campaign = {
        "schema_version": "1.0",
        "source_shard_manifest": str(shard_manifest.resolve()),
        "source_shard_counts": (
            str(shard_counts_path.resolve())
            if shard_counts_path is not None
            else None
        ),
        "wave_size": wave_size,
        "total_shards": len(shard_paths),
        "total_records": sum(shard_record_counts.values()),
        "wave_count": len(waves),
        "waves": waves,
    }
    campaign_path = output_dir / "campaign_manifest.json"
    with open(campaign_path, "w", encoding="utf-8") as file:
        json.dump(campaign, file, indent=2, sort_keys=True)
        file.write("\n")

    print(f"Campaign records: {campaign['total_records']:,}")
    print(f"Task shards: {campaign['total_shards']:,}")
    print(f"Submission waves: {campaign['wave_count']:,}")
    print(f"Campaign manifest: {campaign_path.resolve()}")
    return campaign_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-manifest", required=True, type=Path)
    parser.add_argument("--shard-counts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--wave-size", required=True, type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.wave_size <= 999:
        parser.error("--wave-size must be between 1 and 999")
    create_wave_manifests(
        args.shard_manifest,
        args.shard_counts,
        args.output_dir,
        args.wave_size,
        args.overwrite,
    )


if __name__ == "__main__":
    main()
