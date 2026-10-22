#!/usr/bin/env python3
"""Create final campaign pending and completeness artifacts."""

import argparse
import csv
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine_export import atomic_write_json


PENDING_FIELDS = ["htid", "reason_code", "detail"]
COUNT_FIELDS = [
    "input_records",
    "attempted",
    "successful",
    "processed_new",
    "checkpointed",
    "pending",
]


def write_pending(path: Path, records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=PENDING_FIELDS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            for htid in sorted(records):
                writer.writerow(records[htid])
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def finalize_campaign(
    campaign_manifest: Path,
    pending_dir: Path,
    status_dir: Path,
    final_pending_output: Path,
) -> None:
    with open(campaign_manifest, "r", encoding="utf-8") as file:
        campaign = json.load(file)
    wave_count = int(campaign["wave_count"])

    pending_records = {}
    totals = {field: 0 for field in COUNT_FIELDS}
    total_task_count = 0
    total_task_seconds = 0.0
    for wave_index in range(wave_count):
        tag = f"wave_{wave_index:04d}"
        pending_path = pending_dir / "waves" / f"{tag}.tsv"
        status_path = status_dir / "waves" / f"{tag}.json"
        if not pending_path.is_file() or not status_path.is_file():
            raise RuntimeError(f"Missing finalized artifacts for {tag}")

        with open(pending_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, delimiter="\t")
            if reader.fieldnames != PENDING_FIELDS:
                raise RuntimeError(f"Invalid pending header in {pending_path}")
            for row in reader:
                pending_records[row["htid"]] = row

        with open(status_path, "r", encoding="utf-8") as file:
            status = json.load(file)
        total_task_count += int(status["task_count"])
        total_task_seconds += float(status["summed_task_elapsed_seconds"])
        for field in COUNT_FIELDS:
            totals[field] += int(status[field])

    expected_records = int(campaign["total_records"])
    if totals["attempted"] != expected_records:
        raise RuntimeError(
            f"Campaign expected {expected_records} records but attempted {totals['attempted']}"
        )
    if totals["successful"] + totals["pending"] != expected_records:
        raise RuntimeError("Campaign terminal counts do not match selected input")
    if len(pending_records) != totals["pending"]:
        raise RuntimeError("Consolidated pending records do not match task totals")

    write_pending(final_pending_output, pending_records)
    atomic_write_json(
        status_dir / "campaign_summary.json",
        {
            "schema_version": "1.0",
            "campaign_manifest": str(campaign_manifest.resolve()),
            "wave_count": wave_count,
            "task_count": total_task_count,
            **totals,
            "summed_task_elapsed_seconds": round(total_task_seconds, 3),
            "complete": True,
        },
    )
    print(
        f"Campaign complete: {totals['successful']:,} successful, "
        f"{totals['pending']:,} pending, {expected_records:,} total"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-manifest", required=True, type=Path)
    parser.add_argument("--pending-dir", required=True, type=Path)
    parser.add_argument("--status-dir", required=True, type=Path)
    parser.add_argument("--final-pending-output", required=True, type=Path)
    args = parser.parse_args()
    finalize_campaign(
        args.campaign_manifest,
        args.pending_dir,
        args.status_dir,
        args.final_pending_output,
    )


if __name__ == "__main__":
    main()
