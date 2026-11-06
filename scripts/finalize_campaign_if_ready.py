#!/usr/bin/env python3
"""Finalize a multi-lane campaign after every wave has completed."""

import argparse
import json
import os
import time
from pathlib import Path

from finalize_campaign import finalize_campaign


def campaign_is_ready(
    campaign_manifest: Path,
    pending_dir: Path,
    status_dir: Path,
) -> bool:
    with campaign_manifest.open(encoding="utf-8") as file:
        wave_count = int(json.load(file)["wave_count"])

    for wave_index in range(wave_count):
        tag = f"wave_{wave_index:04d}"
        if not (pending_dir / "waves" / f"{tag}.tsv").is_file():
            return False
        if not (status_dir / "waves" / f"{tag}.json").is_file():
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-manifest", required=True, type=Path)
    parser.add_argument("--pending-dir", required=True, type=Path)
    parser.add_argument("--status-dir", required=True, type=Path)
    parser.add_argument("--final-pending-output", required=True, type=Path)
    args = parser.parse_args()

    lock_dir = args.status_dir / ".campaign-finalization.lock"
    acquired = False
    for _ in range(60):
        try:
            lock_dir.mkdir(parents=True)
            acquired = True
            break
        except FileExistsError:
            time.sleep(2)

    if not acquired:
        raise RuntimeError(f"Could not acquire campaign finalization lock: {lock_dir}")

    try:
        summary_path = args.status_dir / "campaign_summary.json"
        if summary_path.is_file():
            print(f"Campaign already finalized: {summary_path}")
            return
        if not campaign_is_ready(
            args.campaign_manifest,
            args.pending_dir,
            args.status_dir,
        ):
            print("Other campaign lanes are still active; finalization deferred.")
            return
        finalize_campaign(
            args.campaign_manifest,
            args.pending_dir,
            args.status_dir,
            args.final_pending_output,
        )
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
