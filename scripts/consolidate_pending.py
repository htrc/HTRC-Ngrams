#!/usr/bin/env python3
"""Merge one SLURM run's pending shards into one final TSV."""

import argparse
import csv
import os
import shutil
import uuid
from pathlib import Path


PENDING_FIELDS = ["htid", "reason_code", "detail"]


def consolidate_pending(pending_dir: Path, run_id: str, delete_shards: bool) -> Path:
    run_dir = pending_dir / run_id
    shard_paths = sorted(run_dir.glob("task_*.tsv"))
    if not shard_paths:
        raise RuntimeError(f"No pending shards found in {run_dir}")

    records = {}
    for shard_path in shard_paths:
        with open(shard_path, "r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file, delimiter="\t")
            if reader.fieldnames != PENDING_FIELDS:
                raise RuntimeError(
                    f"Unexpected pending header in {shard_path}: {reader.fieldnames}"
                )
            for row in reader:
                htid = (row.get("htid") or "").strip()
                reason_code = (row.get("reason_code") or "").strip()
                if not htid or not reason_code:
                    continue
                records[htid] = {
                    "htid": htid,
                    "reason_code": reason_code,
                    "detail": row.get("detail") or "",
                }

    pending_dir.mkdir(parents=True, exist_ok=True)
    output_path = pending_dir / "pending_volumes.tsv"
    tmp_path = output_path.with_name(
        f"{output_path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
    )

    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as file:
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
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    if delete_shards:
        shutil.rmtree(run_dir)

    print(f"Consolidated {len(records):,} pending volume(s) into {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate task-level pending TSVs after a SLURM array run."
    )
    parser.add_argument("--pending-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--delete-shards", action="store_true")
    args = parser.parse_args()
    consolidate_pending(args.pending_dir, args.run_id, args.delete_shards)


if __name__ == "__main__":
    main()
