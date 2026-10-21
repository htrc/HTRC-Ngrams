#!/usr/bin/env python3
"""Merge one SLURM array's temporary task logs into one run log."""

import argparse
import os
import shutil
import uuid
from pathlib import Path


def consolidate_logs(
    task_log_dir: Path,
    output_path: Path,
    job_id: str,
    delete_task_logs: bool,
) -> Path:
    task_logs = sorted(task_log_dir.glob(f"task_{job_id}_*.log"))
    if not task_logs:
        raise RuntimeError(
            f"No task logs found for SLURM array {job_id} in {task_log_dir}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(
        f"{output_path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
    )

    try:
        with open(tmp_path, "wb") as output_file:
            for task_log in task_logs:
                output_file.write(
                    f"===== {task_log.name} =====\n".encode("utf-8")
                )
                with open(task_log, "rb") as input_file:
                    shutil.copyfileobj(input_file, output_file)
                if task_log.stat().st_size:
                    with open(task_log, "rb") as input_file:
                        input_file.seek(-1, os.SEEK_END)
                        if input_file.read(1) != b"\n":
                            output_file.write(b"\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    if delete_task_logs:
        for task_log in task_logs:
            task_log.unlink()
        try:
            task_log_dir.rmdir()
        except OSError:
            pass

    print(f"Consolidated {len(task_logs):,} task log(s) into {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate one SLURM array's task logs."
    )
    parser.add_argument("--task-log-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--delete-task-logs", action="store_true")
    args = parser.parse_args()

    consolidate_logs(
        args.task_log_dir,
        args.output,
        args.job_id,
        args.delete_task_logs,
    )


if __name__ == "__main__":
    main()
