#!/usr/bin/env python3
"""Append one campaign submission record under an exclusive file lock."""

import argparse
import fcntl
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--wave-index", required=True, type=int)
    parser.add_argument("--array-job-id", required=True)
    parser.add_argument("--finalizer-job-id", required=True)
    args = parser.parse_args()

    args.path.parent.mkdir(parents=True, exist_ok=True)
    with args.path.open("a", encoding="utf-8", newline="") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        file.write(
            f"{args.wave_index}\t{args.array_job_id}\t"
            f"{args.finalizer_job_id}\n"
        )
        file.flush()
        os.fsync(file.fileno())
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
