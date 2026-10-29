#!/usr/bin/env python3
"""Create task-sized, language-homogeneous HTID shards from a Hathifile."""

import argparse
import csv
import heapq
import json
import math
import os
import random
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine_nlp import canonicalize_language, is_supported_language
from src.engine_io import htid_to_zip_path
from src.engine_export import atomic_write_json


CHECKPOINT_SCHEMA_VERSION = "1.0"


def balance_records_by_size(records, target_volumes: int):
    """Balance records with a bounded min-heap in O(N log S) time."""
    if not records:
        return []

    shard_count = math.ceil(len(records) / target_volumes)
    shard_records = [[] for _ in range(shard_count)]
    available_shards = [
        (0, 0, shard_index)
        for shard_index in range(shard_count)
    ]
    heapq.heapify(available_shards)

    for record in sorted(
        records,
        key=lambda item: (-(item[2] or 0), item[0]),
    ):
        total_size, record_count, shard_index = heapq.heappop(
            available_shards
        )
        shard_records[shard_index].append(record)
        total_size += record[2] or 0
        record_count += 1
        if record_count < target_volumes:
            heapq.heappush(
                available_shards,
                (total_size, record_count, shard_index),
            )

    return shard_records


def load_records(input_path: Path, limit: int, sample_seed: int = None):
    if sample_seed is not None and not limit:
        raise RuntimeError("--sample-seed requires a positive --limit")

    if sample_seed is not None:
        reservoir = []
        random_generator = random.Random(sample_seed)
        eligible_count = 0

        with open(input_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            for row in reader:
                if not row:
                    continue
                htid = row[0].strip()
                language = row[1].strip().lower() if len(row) > 1 else ""
                if not htid or htid.lower() == "htid":
                    continue

                eligible_count += 1
                record = (htid, canonicalize_language(language))
                if len(reservoir) < limit:
                    reservoir.append(record)
                    continue

                replacement = random_generator.randrange(eligible_count)
                if replacement < limit:
                    reservoir[replacement] = record

        if eligible_count < limit:
            raise RuntimeError(
                f"Requested {limit:,} records but found only {eligible_count:,}."
            )
        if len({htid for htid, _ in reservoir}) != len(reservoir):
            raise RuntimeError(
                "The sampled Hathifile records contain duplicate HTIDs."
            )
        return reservoir

    records = []
    seen = set()

    with open(input_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            if not row:
                continue

            htid = row[0].strip()
            language = row[1].strip().lower() if len(row) > 1 else ""
            if not htid or htid.lower() == "htid" or htid in seen:
                continue

            seen.add(htid)
            records.append((htid, canonicalize_language(language)))
            if limit and sample_seed is None and len(records) == limit:
                break

    return records


def _atomic_write_text(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="") as file:
            for line in lines:
                file.write(line)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _input_signature(input_path: Path):
    input_stat = input_path.stat()
    return {
        "path": str(input_path.resolve()),
        "size_bytes": input_stat.st_size,
        "mtime_ns": input_stat.st_mtime_ns,
    }


def _checkpoint_configuration(
    input_path: Path,
    limit: int,
    target_volumes: int,
):
    return {
        "input": _input_signature(input_path),
        "limit": limit,
        "target_volumes": target_volumes,
    }


def _load_checkpoint(checkpoint_path: Path, expected_configuration):
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as file:
            checkpoint = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot read sharding checkpoint: {checkpoint_path}"
        ) from exc

    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported checkpoint schema in {checkpoint_path}"
        )
    if checkpoint.get("configuration") != expected_configuration:
        raise RuntimeError(
            "The existing checkpoint was created from different input or "
            "sharding options. Use a new output directory or --overwrite."
        )
    return checkpoint


def _save_checkpoint(checkpoint_path: Path, checkpoint) -> None:
    checkpoint["updated_at_epoch"] = time.time()
    atomic_write_json(checkpoint_path, checkpoint)


def _spool_path(staging_dir: Path, group: str) -> Path:
    return staging_dir / f"{group}.tsv"


def _flush_spool_files(spool_files):
    spool_sizes = {}
    for group, file in spool_files.items():
        file.flush()
        os.fsync(file.fileno())
        spool_sizes[group] = file.tell()
    return spool_sizes


def _restore_staged_records(staging_dir: Path, spool_sizes):
    seen = set()
    language_counts = defaultdict(int)

    for group, saved_size in sorted(spool_sizes.items()):
        path = _spool_path(staging_dir, group)
        if not path.is_file():
            raise RuntimeError(f"Checkpoint spool file is missing: {path}")

        with open(path, "r+b") as file:
            file.truncate(saved_size)

        with open(path, "r", encoding="utf-8", newline="") as file:
            for line_number, line in enumerate(file, start=1):
                row = line.rstrip("\n").split("\t", 1)
                if len(row) != 2:
                    raise RuntimeError(
                        f"Invalid staged record in {path}:{line_number}"
                    )
                htid, language = row
                if htid in seen:
                    raise RuntimeError(
                        f"Duplicate staged HTID detected: {htid}"
                    )
                seen.add(htid)
                language_counts[language] += 1

    return seen, language_counts


def _stage_count_balanced_records(
    input_path: Path,
    staging_dir: Path,
    checkpoint_path: Path,
    checkpoint,
    limit: int,
    checkpoint_interval: int,
):
    spool_sizes = checkpoint.get("spool_sizes", {})
    seen, language_counts = _restore_staged_records(
        staging_dir,
        spool_sizes,
    )
    selected_count = len(seen)
    input_rows_seen = checkpoint.get("input_rows_seen", 0)
    last_checkpoint_row = input_rows_seen
    spool_files = {}

    try:
        for group in spool_sizes:
            spool_files[group] = open(
                _spool_path(staging_dir, group),
                "ab",
            )

        with open(input_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            for physical_row_number, row in enumerate(reader, start=1):
                if physical_row_number <= input_rows_seen:
                    continue

                input_rows_seen = physical_row_number
                if row:
                    htid = row[0].strip()
                    language = row[1].strip().lower() if len(row) > 1 else ""
                    if (
                        htid
                        and htid.lower() != "htid"
                        and htid not in seen
                    ):
                        normalized_language = (
                            canonicalize_language(language) or "unknown"
                        )
                        group = (
                            normalized_language
                            if is_supported_language(normalized_language)
                            else "unsupported"
                        )
                        if group not in spool_files:
                            spool_files[group] = open(
                                _spool_path(staging_dir, group),
                                "ab",
                            )

                        spool_files[group].write(
                            f"{htid}\t{normalized_language}\n".encode("utf-8")
                        )
                        seen.add(htid)
                        language_counts[normalized_language] += 1
                        selected_count += 1

                if (
                    input_rows_seen - last_checkpoint_row
                    >= checkpoint_interval
                ):
                    checkpoint.update(
                        {
                            "phase": "loading",
                            "input_rows_seen": input_rows_seen,
                            "selected_records": selected_count,
                            "spool_sizes": _flush_spool_files(spool_files),
                        }
                    )
                    _save_checkpoint(checkpoint_path, checkpoint)
                    last_checkpoint_row = input_rows_seen
                    print(
                        "Staging progress: "
                        f"{input_rows_seen:,} input row(s), "
                        f"{selected_count:,} unique HTID(s)",
                        flush=True,
                    )

                if limit and selected_count >= limit:
                    break

        if limit and selected_count < limit:
            raise RuntimeError(
                f"Requested {limit:,} unique records but found only "
                f"{selected_count:,}."
            )

        checkpoint.update(
            {
                "phase": "staged",
                "input_rows_seen": input_rows_seen,
                "selected_records": selected_count,
                "spool_sizes": _flush_spool_files(spool_files),
            }
        )
        _save_checkpoint(checkpoint_path, checkpoint)
    finally:
        for file in spool_files.values():
            file.close()

    return selected_count, language_counts


def _count_shard_records(shard_path: Path) -> int:
    with open(shard_path, "r", encoding="utf-8", newline="") as file:
        return max(sum(1 for _ in file) - 1, 0)


def _existing_group_shards(output_dir: Path, group: str):
    indexed_paths = []
    prefix = f"{group}_"
    for path in output_dir.glob(f"{group}_*.tsv"):
        suffix = path.stem[len(prefix):]
        if suffix.isdigit():
            indexed_paths.append((int(suffix), path))

    indexed_paths.sort()
    for expected_index, (actual_index, path) in enumerate(indexed_paths):
        if actual_index != expected_index:
            raise RuntimeError(
                f"Non-contiguous shard sequence for {group}: {path}"
            )
    return [path for _, path in indexed_paths]


def _write_shard_atomically(shard_path: Path, records_for_shard) -> None:
    temporary_path = shard_path.with_name(
        f"{shard_path.name}.{os.getpid()}.tmp"
    )
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n")
            writer.writerow(["htid", "language", "zip_size_bytes"])
            writer.writerows(
                (htid, language, "")
                for htid, language in records_for_shard
            )
        os.replace(temporary_path, shard_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _create_shards_from_staging(
    output_dir: Path,
    staging_dir: Path,
    checkpoint_path: Path,
    checkpoint,
    target_volumes: int,
):
    completed_groups = set(checkpoint.get("completed_groups", []))
    total_shards_written = 0

    for spool_path in sorted(staging_dir.glob("*.tsv")):
        group = spool_path.stem
        if group in completed_groups:
            continue

        existing_shards = _existing_group_shards(output_dir, group)
        processed_records = 0
        if existing_shards:
            processed_records = (
                (len(existing_shards) - 1) * target_volumes
                + _count_shard_records(existing_shards[-1])
            )

        with open(spool_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            for _ in range(processed_records):
                if next(reader, None) is None:
                    raise RuntimeError(
                        f"Existing shards exceed staged records for {group}"
                    )

            shard_index = len(existing_shards)
            while True:
                records_for_shard = []
                for _ in range(target_volumes):
                    row = next(reader, None)
                    if row is None:
                        break
                    if len(row) != 2:
                        raise RuntimeError(
                            f"Invalid staged row for language group {group}"
                        )
                    records_for_shard.append((row[0], row[1]))

                if not records_for_shard:
                    break

                shard_path = output_dir / f"{group}_{shard_index:06d}.tsv"
                _write_shard_atomically(shard_path, records_for_shard)
                shard_index += 1
                total_shards_written += 1
                if total_shards_written % 1000 == 0:
                    print(
                        f"Shard creation progress: "
                        f"{total_shards_written:,} new shard(s)",
                        flush=True,
                    )

        completed_groups.add(group)
        checkpoint.update(
            {
                "phase": "sharding",
                "completed_groups": sorted(completed_groups),
            }
        )
        _save_checkpoint(checkpoint_path, checkpoint)


def _build_count_balanced_manifests(
    output_dir: Path,
    staging_dir: Path,
    target_volumes: int,
    expected_record_count: int,
):
    manifest_rows = []
    for spool_path in sorted(staging_dir.glob("*.tsv")):
        group = spool_path.stem
        group_shards = _existing_group_shards(output_dir, group)
        if not group_shards:
            continue

        for shard_index, shard_path in enumerate(group_shards):
            if shard_index == len(group_shards) - 1:
                record_count = _count_shard_records(shard_path)
            else:
                record_count = target_volumes
            manifest_rows.append((shard_path.resolve(), record_count))

    manifest_record_count = sum(count for _, count in manifest_rows)
    if manifest_record_count != expected_record_count:
        raise RuntimeError(
            "Shard manifest record count does not match staged input: "
            f"{manifest_record_count:,} != {expected_record_count:,}"
        )

    manifest_path = output_dir / "shards_manifest.txt"
    shard_counts_path = output_dir / "shard_counts.tsv"
    _atomic_write_text(
        manifest_path,
        (f"{shard_path}\n" for shard_path, _ in manifest_rows),
    )
    _atomic_write_text(
        shard_counts_path,
        (
            ["path\trecord_count\n"]
            + [
                f"{shard_path}\t{record_count}\n"
                for shard_path, record_count in manifest_rows
            ]
        ),
    )
    return manifest_path, shard_counts_path, manifest_rows


def create_count_balanced_shards(
    input_path: Path,
    output_dir: Path,
    limit: int,
    target_volumes: int,
    overwrite: bool,
    resume: bool,
    checkpoint_interval: int,
) -> Path:
    manifest_path = output_dir / "shards_manifest.txt"
    shard_counts_path = output_dir / "shard_counts.tsv"
    checkpoint_path = output_dir / "sharding_checkpoint.json"
    staging_dir = output_dir / ".staging"
    configuration = _checkpoint_configuration(
        input_path,
        limit,
        target_volumes,
    )

    if output_dir.exists():
        if overwrite:
            shutil.rmtree(output_dir)
        elif not resume:
            raise RuntimeError(
                f"Shard output already exists: {output_dir}. Use --resume to "
                "continue or --overwrite only when replacement is safe."
            )
        elif manifest_path.is_file() != shard_counts_path.is_file():
            raise RuntimeError(
                "Shard output contains only one final manifest file. "
                "Restore the missing file or use a new output directory."
            )

        if resume and not checkpoint_path.is_file():
            existing_entries = list(output_dir.iterdir())
            if existing_entries:
                raise RuntimeError(
                    "Shard output contains files but no durable checkpoint. "
                    "Use a new output directory or inspect the existing data "
                    "before using --overwrite."
                )

    output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    for temporary_path in output_dir.glob("*.tmp"):
        temporary_path.unlink()

    if checkpoint_path.is_file():
        if not resume:
            raise RuntimeError(
                f"Checkpoint already exists: {checkpoint_path}. Use --resume."
            )
        checkpoint = _load_checkpoint(checkpoint_path, configuration)
        if checkpoint.get("phase") == "complete":
            if not manifest_path.is_file() or not shard_counts_path.is_file():
                raise RuntimeError(
                    "The sharding checkpoint is complete, but one or more "
                    "final manifest files are missing."
                )
            print(f"Sharding is already complete: {manifest_path.resolve()}")
            return manifest_path
        print(
            "Resuming sharding from "
            f"{checkpoint.get('input_rows_seen', 0):,} input row(s)",
            flush=True,
        )
    else:
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "configuration": configuration,
            "phase": "loading",
            "input_rows_seen": 0,
            "selected_records": 0,
            "spool_sizes": {},
            "completed_groups": [],
        }
        _save_checkpoint(checkpoint_path, checkpoint)

    if checkpoint.get("phase") == "loading":
        selected_count, language_counts = _stage_count_balanced_records(
            input_path,
            staging_dir,
            checkpoint_path,
            checkpoint,
            limit,
            checkpoint_interval,
        )
    else:
        _, language_counts = _restore_staged_records(
            staging_dir,
            checkpoint.get("spool_sizes", {}),
        )
        selected_count = sum(language_counts.values())

    if selected_count == 0:
        raise RuntimeError(f"No HTID/language records found in {input_path}")

    _create_shards_from_staging(
        output_dir,
        staging_dir,
        checkpoint_path,
        checkpoint,
        target_volumes,
    )
    manifest_path, shard_counts_path, manifest_rows = (
        _build_count_balanced_manifests(
            output_dir,
            staging_dir,
            target_volumes,
            selected_count,
        )
    )

    checkpoint.update(
        {
            "phase": "complete",
            "selected_records": selected_count,
            "task_shards": len(manifest_rows),
        }
    )
    _save_checkpoint(checkpoint_path, checkpoint)
    shutil.rmtree(staging_dir)

    supported = sum(
        count
        for language, count in language_counts.items()
        if is_supported_language(language)
    )
    unsupported = selected_count - supported
    print(f"Selected records: {selected_count:,}")
    print(f"Languages: {len(language_counts):,}")
    print(f"Supported records: {supported:,}")
    print(f"Unsupported records retained for pending reporting: {unsupported:,}")
    print(f"Task shards: {len(manifest_rows):,}")
    print(f"Shard manifest: {manifest_path.resolve()}")
    print(f"Shard counts: {shard_counts_path.resolve()}")

    for language in sorted(language_counts):
        status = "supported" if is_supported_language(language) else "unsupported"
        print(
            f"  {language}: {language_counts[language]:,} "
            f"record(s), {status}"
        )

    return manifest_path


def create_shards(
    input_path: Path,
    output_dir: Path,
    limit: int,
    target_volumes: int,
    sample_seed: int,
    pairtree_root: Path,
    isolate_zip_size_mb: float,
    overwrite: bool,
    resume: bool,
    checkpoint_interval: int,
) -> Path:
    if pairtree_root is None and sample_seed is None:
        return create_count_balanced_shards(
            input_path,
            output_dir,
            limit,
            target_volumes,
            overwrite,
            resume,
            checkpoint_interval,
        )

    if resume:
        raise RuntimeError(
            "--resume is currently supported only for count-balanced runs "
            "without --pairtree-root or --sample-seed."
        )

    if output_dir.exists():
        if not overwrite:
            raise RuntimeError(
                f"Shard output already exists: {output_dir}. "
                "Use --overwrite only when it is safe to replace it."
            )
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True)
    records = load_records(input_path, limit, sample_seed)
    if not records:
        raise RuntimeError(f"No HTID/language records found in {input_path}")
    if limit and len(records) < limit:
        raise RuntimeError(
            f"Requested {limit:,} unique records but found only {len(records):,}."
        )

    records_by_language = defaultdict(list)
    language_counts = defaultdict(int)
    missing_zip_count = 0
    for htid, language in records:
        normalized_language = language or "unknown"
        language_counts[normalized_language] += 1
        group = (
            normalized_language
            if is_supported_language(normalized_language)
            else "unsupported"
        )
        zip_size_bytes = None
        if pairtree_root is not None:
            try:
                zip_size_bytes = htid_to_zip_path(
                    htid, pairtree_root
                ).stat().st_size
            except FileNotFoundError:
                missing_zip_count += 1
        records_by_language[group].append(
            (htid, normalized_language, zip_size_bytes)
        )

    manifest_path = output_dir / "shards_manifest.txt"
    shard_counts_path = output_dir / "shard_counts.tsv"
    manifest_rows = []
    isolated_zip_count = 0
    isolation_threshold_bytes = (
        int(isolate_zip_size_mb * 1024 * 1024)
        if isolate_zip_size_mb > 0
        else None
    )

    for language in sorted(records_by_language):
        language_records = records_by_language[language]
        if isolation_threshold_bytes is not None:
            regular_records = [
                record
                for record in language_records
                if record[2] is None or record[2] <= isolation_threshold_bytes
            ]
            isolated_records = [
                record
                for record in language_records
                if record[2] is not None and record[2] > isolation_threshold_bytes
            ]
        else:
            regular_records = language_records
            isolated_records = []

        if pairtree_root is not None:
            shard_records = balance_records_by_size(
                regular_records,
                target_volumes,
            )
        else:
            shard_records = [
                regular_records[index:index + target_volumes]
                for index in range(0, len(regular_records), target_volumes)
            ]

        shard_records.extend([[record] for record in isolated_records])
        isolated_zip_count += len(isolated_records)

        for shard_index, records_for_shard in enumerate(shard_records):
            shard_path = output_dir / f"{language}_{shard_index:04d}.tsv"

            with open(shard_path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="\t", lineterminator="\n")
                writer.writerow(["htid", "language", "zip_size_bytes"])
                writer.writerows(records_for_shard)

            manifest_rows.append((shard_path.resolve(), len(records_for_shard)))

    with open(manifest_path, "w", encoding="utf-8", newline="") as file:
        for shard_path, _ in manifest_rows:
            file.write(f"{shard_path}\n")

    with open(shard_counts_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        writer.writerow(["path", "record_count"])
        writer.writerows(manifest_rows)

    supported = sum(
        count
        for language, count in language_counts.items()
        if is_supported_language(language)
    )
    unsupported = len(records) - supported

    print(f"Selected records: {len(records):,}")
    print(f"Languages: {len(language_counts):,}")
    print(f"Supported records: {supported:,}")
    print(f"Unsupported records retained for pending reporting: {unsupported:,}")
    print(f"Task shards: {len(manifest_rows):,}")
    if pairtree_root is not None:
        print(f"Records missing from Pairtree during sharding: {missing_zip_count:,}")
        print(
            "Large ZIPs isolated into one-volume shards: "
            f"{isolated_zip_count:,}"
        )
    print(f"Shard manifest: {manifest_path.resolve()}")
    print(f"Shard counts: {shard_counts_path.resolve()}")

    for language in sorted(language_counts):
        status = "supported" if is_supported_language(language) else "unsupported"
        print(
            f"  {language}: {language_counts[language]:,} "
            f"record(s), {status}"
        )

    return manifest_path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create language-specific task shards from a tab-separated "
            "Hathifile whose first two columns are HTID and language."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-volumes", type=int, default=100)
    parser.add_argument("--sample-seed", type=int)
    parser.add_argument("--pairtree-root", type=Path)
    parser.add_argument(
        "--isolate-zip-size-mb",
        type=float,
        default=150.0,
        help=(
            "Place ZIPs larger than this size into one-volume shards without "
            "rejecting them. Use 0 to disable isolation (default: 150)."
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume count-balanced sharding from its durable checkpoint. "
            "This is not available with --pairtree-root or --sample-seed."
        ),
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=1000000,
        help="Save count-balanced loading progress every N input rows.",
    )
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit cannot be negative")
    if args.target_volumes <= 0:
        parser.error("--target-volumes must be greater than zero")
    if args.isolate_zip_size_mb < 0:
        parser.error("--isolate-zip-size-mb cannot be negative")
    if args.checkpoint_interval <= 0:
        parser.error("--checkpoint-interval must be greater than zero")
    if args.overwrite and args.resume:
        parser.error("--overwrite and --resume cannot be used together")

    create_shards(
        args.input,
        args.output_dir,
        args.limit,
        args.target_volumes,
        args.sample_seed,
        args.pairtree_root,
        args.isolate_zip_size_mb,
        args.overwrite,
        args.resume,
        args.checkpoint_interval,
    )


if __name__ == "__main__":
    main()
