#!/usr/bin/env python3
"""Build and verify a shard manifest for an unfinished campaign suffix."""

import argparse
import csv
import json
import os
import uuid
from pathlib import Path


PENDING_FIELDS = ["htid", "reason_code", "detail"]


def normalize_path(value: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(value)))


def atomic_write_lines(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="") as file:
            file.writelines(lines)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_lines(
        path,
        [json.dumps(payload, indent=2, sort_keys=True), "\n"],
    )


def wave_is_finalized(wave_index: int, pending_dir: Path, status_dir: Path) -> bool:
    tag = f"wave_{wave_index:04d}"
    pending_path = pending_dir / f"{tag}.tsv"
    status_path = status_dir / f"{tag}.json"
    if not (
        pending_path.is_file()
        and pending_path.stat().st_size > 0
        and status_path.is_file()
        and status_path.stat().st_size > 0
    ):
        return False

    try:
        with status_path.open(encoding="utf-8") as file:
            status = json.load(file)
        input_records = int(status["input_records"])
        attempted = int(status["attempted"])
        successful = int(status["successful"])
        processed_new = int(status["processed_new"])
        checkpointed = int(status["checkpointed"])
        pending = int(status["pending"])
        if attempted != input_records:
            return False
        if attempted != successful + pending:
            return False
        if successful != processed_new + checkpointed:
            return False

        pending_rows = 0
        pending_htids = set()
        with pending_path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, delimiter="\t")
            if reader.fieldnames != PENDING_FIELDS:
                return False
            for row in reader:
                htid = (row.get("htid") or "").strip()
                reason_code = (row.get("reason_code") or "").strip()
                if not htid or not reason_code or htid in pending_htids:
                    return False
                pending_htids.add(htid)
                pending_rows += 1
        return pending_rows == pending
    except (
        csv.Error,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def detect_start_wave(wave_count: int, pending_dir: Path, status_dir: Path) -> int:
    for wave_index in range(wave_count):
        if not wave_is_finalized(wave_index, pending_dir, status_dir):
            return wave_index
    raise SystemExit("Every source-campaign wave is already finalized.")


def load_wave_paths(campaign: dict):
    waves = campaign.get("waves")
    wave_count = int(campaign["wave_count"])
    if not isinstance(waves, list) or len(waves) != wave_count:
        raise RuntimeError("Campaign wave count does not match its wave list")

    expected_indices = list(range(wave_count))
    actual_indices = [int(wave["wave_index"]) for wave in waves]
    if actual_indices != expected_indices:
        raise RuntimeError("Campaign wave indexes are incomplete or out of order")

    wave_paths = []
    all_paths = set()
    for wave in waves:
        manifest_path = normalize_path(wave["manifest"])
        with manifest_path.open(encoding="utf-8") as file:
            paths = [
                normalize_path(line.strip())
                for line in file
                if line.strip()
            ]
        expected_tasks = int(wave["task_count"])
        if len(paths) != expected_tasks:
            raise RuntimeError(
                f"{manifest_path} contains {len(paths)} paths; "
                f"expected {expected_tasks}"
            )
        duplicates = all_paths.intersection(paths)
        if duplicates:
            raise RuntimeError(
                f"Duplicate shard across campaign waves: {next(iter(duplicates))}"
            )
        if len(set(paths)) != len(paths):
            raise RuntimeError(f"Duplicate shard within wave: {manifest_path}")
        all_paths.update(paths)
        wave_paths.append(paths)

    expected_shards = int(campaign["total_shards"])
    if len(all_paths) != expected_shards:
        raise RuntimeError(
            f"Campaign expected {expected_shards:,} unique shards; "
            f"found {len(all_paths):,}"
        )
    return wave_paths, all_paths


def load_shard_counts(path: Path) -> dict:
    counts = {}
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        if reader.fieldnames != ["path", "record_count"]:
            raise RuntimeError(f"Invalid shard-count header in {path}")
        for row in reader:
            shard_path = normalize_path(row["path"])
            if shard_path in counts:
                raise RuntimeError(f"Duplicate shard count: {shard_path}")
            record_count = int(row["record_count"])
            if record_count <= 0:
                raise RuntimeError(
                    f"Non-positive record count for {shard_path}: {record_count}"
                )
            counts[shard_path] = record_count
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-manifest", required=True, type=Path)
    parser.add_argument("--start-wave", type=int)
    parser.add_argument("--pending-wave-dir", type=Path)
    parser.add_argument("--status-wave-dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if (args.pending_wave_dir is None) != (args.status_wave_dir is None):
        parser.error("--pending-wave-dir and --status-wave-dir must be used together")
    if args.start_wave is None and args.pending_wave_dir is None:
        parser.error(
            "provide --start-wave or both report directories for automatic detection"
        )

    with args.campaign_manifest.open(encoding="utf-8") as file:
        campaign = json.load(file)

    wave_count = int(campaign["wave_count"])
    detected_start = None
    if args.pending_wave_dir is not None:
        detected_start = detect_start_wave(
            wave_count,
            args.pending_wave_dir,
            args.status_wave_dir,
        )
    start_wave = args.start_wave if args.start_wave is not None else detected_start
    if not 0 <= start_wave < wave_count:
        raise SystemExit(f"start-wave must be between 0 and {wave_count - 1}")
    if detected_start is not None and start_wave != detected_start:
        raise RuntimeError(
            f"Requested start wave {start_wave}, but the first unfinished wave "
            f"is {detected_start}"
        )

    manifest_output = args.output_dir / "shards_manifest.txt"
    counts_output = args.output_dir / "shard_counts.tsv"
    summary_output = args.output_dir / "handoff_summary.json"
    outputs = (manifest_output, counts_output, summary_output)
    if not args.overwrite and any(path.exists() for path in outputs):
        raise SystemExit(f"Output already exists in {args.output_dir}")

    wave_paths, all_paths = load_wave_paths(campaign)
    prefix_paths = [
        path
        for paths in wave_paths[:start_wave]
        for path in paths
    ]
    remaining_paths = [
        path
        for paths in wave_paths[start_wave:]
        for path in paths
    ]
    prefix_set = set(prefix_paths)
    remaining_set = set(remaining_paths)
    if prefix_set.intersection(remaining_set):
        raise RuntimeError("Completed prefix and remaining suffix overlap")
    if prefix_set.union(remaining_set) != all_paths:
        raise RuntimeError("Handoff coverage does not match the source campaign")

    source_counts_value = campaign.get("source_shard_counts")
    if not source_counts_value:
        raise RuntimeError("Source campaign does not record a shard-count file")
    source_counts_path = normalize_path(source_counts_value)
    counts = load_shard_counts(source_counts_path)
    if set(counts) != all_paths:
        missing = all_paths - set(counts)
        extra = set(counts) - all_paths
        detail = []
        if missing:
            detail.append(f"missing count for {next(iter(missing))}")
        if extra:
            detail.append(f"unexpected count for {next(iter(extra))}")
        raise RuntimeError(f"Shard-count coverage mismatch: {'; '.join(detail)}")

    source_records = sum(counts.values())
    expected_records = int(campaign["total_records"])
    if source_records != expected_records:
        raise RuntimeError(
            f"Campaign expected {expected_records:,} records; "
            f"shard counts contain {source_records:,}"
        )
    remaining_records = sum(counts[path] for path in remaining_paths)

    finalized_suffix_waves = []
    if args.pending_wave_dir is not None:
        finalized_suffix_waves = [
            wave_index
            for wave_index in range(start_wave, wave_count)
            if wave_is_finalized(
                wave_index,
                args.pending_wave_dir,
                args.status_wave_dir,
            )
        ]

    atomic_write_lines(
        manifest_output,
        (f"{path}\n" for path in remaining_paths),
    )
    atomic_write_lines(
        counts_output,
        ["path\trecord_count\n"] + [
            f"{path}\t{counts[path]}\n"
            for path in remaining_paths
        ],
    )
    atomic_write_json(
        summary_output,
        {
            "schema_version": "1.0",
            "source_campaign_manifest": str(
                normalize_path(args.campaign_manifest)
            ),
            "source_wave_count": wave_count,
            "source_shards": len(all_paths),
            "source_records": source_records,
            "first_unfinished_wave": start_wave,
            "completed_prefix_waves": start_wave,
            "finalized_waves_reincluded": finalized_suffix_waves,
            "remaining_shards": len(remaining_paths),
            "remaining_records": remaining_records,
            "coverage_verified": True,
        },
    )

    print(f"Starting source wave: {start_wave}")
    print(f"Remaining shards: {len(remaining_paths):,}")
    print(f"Remaining records: {remaining_records:,}")
    print("Coverage verification: PASSED")
    if finalized_suffix_waves:
        print(
            "Finalized out-of-order waves safely reincluded: "
            + ", ".join(str(index) for index in finalized_suffix_waves)
        )
    print(f"Shard manifest: {manifest_output.resolve()}")
    print(f"Shard counts: {counts_output.resolve()}")
    print(f"Handoff summary: {summary_output.resolve()}")


if __name__ == "__main__":
    main()
