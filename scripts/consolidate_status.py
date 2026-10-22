#!/usr/bin/env python3
"""Validate and consolidate task completion records for one SLURM wave."""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine_export import atomic_write_json


COUNT_FIELDS = [
    "input_records",
    "attempted",
    "successful",
    "processed_new",
    "checkpointed",
    "pending",
]


def consolidate_status(
    status_dir: Path,
    run_id: str,
    expected_tasks: int,
    output_path: Path,
    delete_shards: bool,
) -> Path:
    run_dir = status_dir / run_id
    paths = sorted(run_dir.glob("task_*.json"))
    if len(paths) != expected_tasks:
        raise RuntimeError(
            f"Expected {expected_tasks} task status files in {run_dir}, found {len(paths)}"
        )

    totals = {field: 0 for field in COUNT_FIELDS}
    task_ids = set()
    elapsed_seconds = 0.0
    for path in paths:
        with open(path, "r", encoding="utf-8") as file:
            record = json.load(file)
        task_id = int(record["task_id"])
        if task_id in task_ids:
            raise RuntimeError(f"Duplicate task status for task {task_id}")
        task_ids.add(task_id)
        if record["attempted"] != record["successful"] + record["pending"]:
            raise RuntimeError(f"Incomplete terminal counts in {path}")
        if record["attempted"] != record["input_records"]:
            raise RuntimeError(f"Task did not attempt every input record: {path}")
        for field in COUNT_FIELDS:
            totals[field] += int(record[field])
        elapsed_seconds += float(record.get("elapsed_seconds", 0.0))

    expected_ids = set(range(expected_tasks))
    if task_ids != expected_ids:
        raise RuntimeError("Task status IDs are incomplete or out of range")

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "task_count": expected_tasks,
        **totals,
        "summed_task_elapsed_seconds": round(elapsed_seconds, 3),
    }
    atomic_write_json(output_path, payload)
    if delete_shards:
        shutil.rmtree(run_dir)
    print(f"Validated {totals['attempted']:,} terminal volume records")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-tasks", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--delete-shards", action="store_true")
    args = parser.parse_args()
    consolidate_status(
        args.status_dir,
        args.run_id,
        args.expected_tasks,
        args.output,
        args.delete_shards,
    )


if __name__ == "__main__":
    main()
