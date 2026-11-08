#!/usr/bin/env python3
"""Submit continuous campaign waves within the user's Slurm queue budget."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import pwd
import subprocess
import time
from pathlib import Path


SUBMISSION_FIELDS = ["wave_index", "array_job_id", "finalizer_job_id"]
TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}


def read_submissions(path: Path, wave_count: int) -> dict[int, list[dict[str, str]]]:
    submissions = {index: [] for index in range(wave_count)}
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        if reader.fieldnames != SUBMISSION_FIELDS:
            raise RuntimeError(f"Unexpected submission header in {path}")
        for row in reader:
            wave_index = int(row["wave_index"])
            if wave_index not in submissions:
                raise RuntimeError(f"Out-of-range wave in {path}: {wave_index}")
            if not row["array_job_id"].isdigit() or not row["finalizer_job_id"].isdigit():
                raise RuntimeError(f"Invalid Slurm job ID in {path}: {row}")
            submissions[wave_index].append(row)
    return submissions


def reconcile_intent(path: Path, submissions: dict[int, list[dict[str, str]]]) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as file:
        intent = json.load(file)
    wave_index = int(intent["wave_index"])
    prior_attempts = int(intent["prior_attempts"])
    if len(submissions[wave_index]) == prior_attempts + 1:
        path.unlink()
        print(f"Recovered recorded submission for wave {wave_index}", flush=True)
        return
    raise RuntimeError(
        f"Ambiguous submission for wave {wave_index}: {path}. "
        "Check Slurm and submissions.tsv before restarting the dispatcher."
    )


def create_intent(path: Path, wave_index: int, prior_attempts: int) -> None:
    payload = {"wave_index": wave_index, "prior_attempts": prior_attempts}
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(payload, file)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def active_jobs(user: str) -> tuple[set[str], int]:
    result = subprocess.run(
        ["squeue", "-r", "-h", "-u", user, "-o", "%i"],
        check=True,
        capture_output=True,
        text=True,
    )
    job_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return set(job_ids), len(job_ids)


def finalizer_state(job_id: str) -> str | None:
    result = subprocess.run(
        ["sacct", "-X", "-n", "-P", "-j", job_id, "--format=JobID,State"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split("|", 1)
        if len(fields) == 2 and fields[0] == job_id:
            state = fields[1].split()
            return state[0].split("+")[0] if state else None
    return None


def wave_reports(pending_dir: Path, status_dir: Path) -> set[int]:
    pending_names = {path.name for path in (pending_dir / "waves").glob("wave_*.tsv")}
    status_names = {path.name for path in (status_dir / "waves").glob("wave_*.json")}
    return {
        int(name[5:-4])
        for name in pending_names
        if f"{name[:-4]}.json" in status_names
    }


def next_wave(
    waves: list[dict],
    submissions: dict[int, list[dict[str, str]]],
    ready: set[int],
    active_ids: set[str],
    terminal_seen: dict[int, float],
    max_attempts: int,
) -> tuple[int, int] | None:
    retry_candidate = None
    unobserved_job = False
    in_flight = False
    exhausted_waves = []
    for wave in waves:
        wave_index = int(wave["wave_index"])
        if wave_index in ready:
            terminal_seen.pop(wave_index, None)
            continue
        attempts = submissions[wave_index]
        if not attempts:
            continue
        latest = attempts[-1]
        array_id = latest["array_job_id"]
        if latest["finalizer_job_id"] in active_ids or any(
            job_id == array_id or job_id.startswith(f"{array_id}_")
            for job_id in active_ids
        ):
            terminal_seen.pop(wave_index, None)
            in_flight = True
            continue
        state = finalizer_state(latest["finalizer_job_id"])
        if state not in TERMINAL_STATES:
            terminal_seen.pop(wave_index, None)
            unobserved_job = True
            in_flight = True
            continue
        first_seen = terminal_seen.setdefault(wave_index, time.monotonic())
        if time.monotonic() - first_seen < 120:
            in_flight = True
            continue
        if len(attempts) >= max_attempts:
            exhausted_waves.append(wave_index)
            continue
        if retry_candidate is None:
            retry_candidate = (wave_index, len(attempts) + 1)

    if unobserved_job:
        return None
    if retry_candidate is not None:
        return retry_candidate

    for wave in waves:
        wave_index = int(wave["wave_index"])
        if wave_index not in ready and not submissions[wave_index]:
            return wave_index, 1
    if exhausted_waves and not in_flight:
        raise RuntimeError(
            f"Waves {exhausted_waves} have no complete pending/status reports "
            f"after {max_attempts} attempts; campaign cannot be finalized"
        )
    return None


def submit_wave(
    wave_index: int,
    attempt: int,
    submissions_path: Path,
    intent_path: Path,
    wave_count: int,
) -> dict[str, str]:
    before = read_submissions(submissions_path, wave_count)
    prior_attempts = len(before[wave_index])
    if attempt != prior_attempts + 1:
        raise RuntimeError(f"Submission attempt changed for wave {wave_index}")
    create_intent(intent_path, wave_index, prior_attempts)
    environment = os.environ.copy()
    environment["CONTINUOUS_DISPATCHER"] = "1"
    environment["WAVE_ATTEMPT"] = str(attempt)
    if attempt > 1:
        wave_tag = f"wave_{wave_index:04d}"
        for report in (
            Path(environment["CAMPAIGN_PENDING_DIR"]) / "waves" / f"{wave_tag}.tsv",
            Path(environment["CAMPAIGN_STATUS_DIR"]) / "waves" / f"{wave_tag}.json",
        ):
            report.unlink(missing_ok=True)
        environment["CAMPAIGN_FRESH_RUN"] = "0"
        environment["TASK_MEMORY"] = environment["RETRY_TASK_MEMORY"]
    else:
        environment["TASK_MEMORY"] = environment["PRIMARY_TASK_MEMORY"]
    subprocess.run(["bash", "submit_wave.sh", str(wave_index)], check=True, env=environment)
    after = read_submissions(submissions_path, wave_count)
    if len(after[wave_index]) != prior_attempts + 1:
        raise RuntimeError(f"Wave {wave_index} was submitted without a ledger record")
    intent_path.unlink()
    return after[wave_index][-1]


def finalize_campaign(manifest: Path, pending_dir: Path, status_dir: Path) -> None:
    subprocess.run(
        [
            "python3",
            "scripts/finalize_campaign_if_ready.py",
            "--campaign-manifest", str(manifest),
            "--pending-dir", str(pending_dir),
            "--status-dir", str(status_dir),
            "--final-pending-output", os.environ["CAMPAIGN_FINAL_PENDING_OUTPUT"],
        ],
        check=True,
    )
    if not (status_dir / "campaign_summary.json").is_file():
        raise RuntimeError("Campaign finalization did not produce a summary")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, required=True)
    args = parser.parse_args()

    with Path(os.environ["DISPATCH_CONFIG"]).open(encoding="utf-8") as file:
        configuration = json.load(file)
    if configuration.get("DISPATCH_MODE") != "continuous":
        raise RuntimeError("Dispatch configuration is not continuous")
    for name, value in configuration.items():
        if not isinstance(value, str):
            raise RuntimeError(f"Invalid dispatch configuration value: {name}")
        os.environ[name] = value

    manifest = Path(os.environ["CAMPAIGN_MANIFEST"])
    control_dir = Path(os.environ["CAMPAIGN_CONTROL_DIR"])
    pending_dir = Path(os.environ["CAMPAIGN_PENDING_DIR"])
    status_dir = Path(os.environ["CAMPAIGN_STATUS_DIR"])
    submissions_path = control_dir / "submissions.tsv"
    intent_path = control_dir / "dispatch_intent.json"
    summary_path = status_dir / "campaign_summary.json"
    queue_budget = int(os.environ["QUEUE_BUDGET"])
    max_attempts = int(os.environ["MAX_WAVE_ATTEMPTS"])
    user = pwd.getpwuid(os.getuid()).pw_name
    if not 1 <= queue_budget <= 980:
        raise RuntimeError("QUEUE_BUDGET must be between 1 and 980")
    if (control_dir / "dispatch_mode").read_text(encoding="utf-8").strip() != "continuous":
        raise RuntimeError("Campaign was not created for continuous dispatch")

    with manifest.open(encoding="utf-8") as file:
        campaign = json.load(file)
    waves = campaign["waves"]
    wave_count = int(campaign["wave_count"])
    if len(waves) != wave_count or not waves:
        raise RuntimeError("Invalid campaign wave count")

    with (control_dir / "dispatcher.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if summary_path.is_file():
            print(f"Campaign already complete: {summary_path}", flush=True)
            return
        reconcile_intent(intent_path, read_submissions(submissions_path, wave_count))
        successor = subprocess.run(
            [
                "sbatch", "--parsable",
                f"--dependency=afterok:{os.environ['SLURM_JOB_ID']}",
                "--export=ALL",
                f"--output={os.environ['RUN_ROOT']}/logs/dispatch_"
                f"{os.environ['CAMPAIGN_NAME']}_%j.log",
                "dispatch_campaign.slurm",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Dispatcher successor: {successor.stdout.strip()}", flush=True)

        deadline = time.monotonic() + args.duration_seconds
        terminal_seen = {}
        last_completed = -1
        awaiting_visibility = None
        last_heartbeat = 0.0
        while time.monotonic() < deadline:
            submissions = read_submissions(submissions_path, wave_count)
            reconcile_intent(intent_path, submissions)
            ready = wave_reports(pending_dir, status_dir)
            completed = len(ready)
            if completed != last_completed:
                print(f"Completed waves: {completed}/{wave_count}", flush=True)
                last_completed = completed
            if completed == wave_count:
                finalize_campaign(manifest, pending_dir, status_dir)
                return

            try:
                active_ids, active_count = active_jobs(user)
                if time.monotonic() - last_heartbeat >= 900:
                    print(
                        f"Dispatcher heartbeat: {completed}/{wave_count} waves "
                        f"complete, {active_count}/{queue_budget} user jobs submitted",
                        flush=True,
                    )
                    last_heartbeat = time.monotonic()
                if awaiting_visibility is not None:
                    wave_index, array_id, finalizer_id, submitted_at = awaiting_visibility
                    visible = finalizer_id in active_ids or any(
                        job_id == array_id or job_id.startswith(f"{array_id}_")
                        for job_id in active_ids
                    )
                    if visible or wave_index in ready:
                        awaiting_visibility = None
                    elif time.monotonic() - submitted_at > 300:
                        raise RuntimeError(
                            f"Submitted wave {wave_index} is absent from Slurm "
                            "and has no complete reports"
                        )
                    else:
                        time.sleep(30)
                        continue
                candidate = next_wave(
                    waves, submissions, ready, active_ids, terminal_seen, max_attempts
                )
            except subprocess.CalledProcessError as error:
                print(f"Slurm status unavailable: {error}", flush=True)
                time.sleep(60)
                continue

            if candidate is not None:
                wave_index, attempt = candidate
                required_slots = int(waves[wave_index]["task_count"]) + 1
                if active_count + required_slots <= queue_budget:
                    print(
                        f"Queue {active_count}/{queue_budget}; submitting wave "
                        f"{wave_index} attempt {attempt}",
                        flush=True,
                    )
                    submission = submit_wave(
                        wave_index, attempt, submissions_path, intent_path, wave_count
                    )
                    awaiting_visibility = (
                        wave_index,
                        submission["array_job_id"],
                        submission["finalizer_job_id"],
                        time.monotonic(),
                    )
                    continue
            time.sleep(60)
        print("Dispatcher handoff: time window ended", flush=True)


if __name__ == "__main__":
    main()
