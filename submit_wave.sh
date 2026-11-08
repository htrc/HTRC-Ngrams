#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WAVE_INDEX="${1:?Usage: submit_wave.sh WAVE_INDEX}"
: "${CAMPAIGN_MANIFEST:?CAMPAIGN_MANIFEST is required}"
: "${RUN_ROOT:?RUN_ROOT is required}"
: "${CAMPAIGN_NAME:?CAMPAIGN_NAME is required}"
: "${CAMPAIGN_CONTROL_DIR:?CAMPAIGN_CONTROL_DIR is required}"
: "${CAMPAIGN_PENDING_DIR:?CAMPAIGN_PENDING_DIR is required}"
: "${CAMPAIGN_STATUS_DIR:?CAMPAIGN_STATUS_DIR is required}"
: "${PROVENANCE_RUN_ID:?PROVENANCE_RUN_ID is required}"

MAX_CONCURRENT_TASKS="${MAX_CONCURRENT_TASKS:-500}"
HIGHPRIO_MAX_CONCURRENT_TASKS=500
DISPATCH_MODE="${DISPATCH_MODE:-lanes}"
CONCURRENCY_FILE="${CONCURRENCY_FILE:-$CAMPAIGN_CONTROL_DIR/max_concurrent_tasks}"
TASK_CPUS="${TASK_CPUS:-8}"
TASK_MEMORY="${TASK_MEMORY:-16GB}"
PRIMARY_TASK_MEMORY="${PRIMARY_TASK_MEMORY:-$TASK_MEMORY}"
RETRY_TASK_MEMORY="${RETRY_TASK_MEMORY:-32GB}"
CAMPAIGN_FRESH_RUN="${CAMPAIGN_FRESH_RUN:-0}"
WAVE_STRIDE="${WAVE_STRIDE:-1}"
CAMPAIGN_LANES="${CAMPAIGN_LANES:-$WAVE_STRIDE}"
WAVE_ATTEMPT="${WAVE_ATTEMPT:-1}"
MAX_WAVE_ATTEMPTS="${MAX_WAVE_ATTEMPTS:-2}"
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"
STATUS_DIR="${STATUS_DIR:-$RUN_ROOT/status}"
RUN_LOG_DIR="${RUN_LOG_DIR:-$RUN_ROOT/logs}"
CAMPAIGN_FINAL_PENDING_OUTPUT="${CAMPAIGN_FINAL_PENDING_OUTPUT:-$CAMPAIGN_PENDING_DIR/pending_volumes.tsv}"

if [ "$DISPATCH_MODE" != "lanes" ] && [ "$DISPATCH_MODE" != "continuous" ]; then
    echo "DISPATCH_MODE must be lanes or continuous" >&2
    exit 1
fi
if [ "$DISPATCH_MODE" = "continuous" ] && [ "${CONTINUOUS_DISPATCHER:-0}" != "1" ]; then
    echo "Continuous waves must be submitted by the campaign dispatcher" >&2
    exit 1
fi
if [ "$DISPATCH_MODE" = "lanes" ] && [ -f "$CONCURRENCY_FILE" ]; then
    MAX_CONCURRENT_TASKS=$(tr -d '[:space:]' < "$CONCURRENCY_FILE")
fi
if ! [[ "$MAX_CONCURRENT_TASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Invalid concurrency value: $MAX_CONCURRENT_TASKS" >&2
    exit 1
fi
if [ "$MAX_CONCURRENT_TASKS" -gt "$HIGHPRIO_MAX_CONCURRENT_TASKS" ]; then
    echo "Concurrency cannot exceed the approved highprio limit of $HIGHPRIO_MAX_CONCURRENT_TASKS" >&2
    exit 1
fi
if ! [[ "$CAMPAIGN_LANES" =~ ^[1-9][0-9]*$ ]]; then
    echo "CAMPAIGN_LANES must be a positive integer" >&2
    exit 1
fi
if [ "$DISPATCH_MODE" = "lanes" ] &&
    [ $((MAX_CONCURRENT_TASKS * CAMPAIGN_LANES)) -gt "$HIGHPRIO_MAX_CONCURRENT_TASKS" ]; then
    echo "Combined lane concurrency cannot exceed the approved highprio limit of $HIGHPRIO_MAX_CONCURRENT_TASKS" >&2
    exit 1
fi

if ! [[ "$TASK_CPUS" =~ ^[1-9][0-9]*$ ]]; then
    echo "TASK_CPUS must be a positive integer" >&2
    exit 1
fi
if [ -z "$TASK_MEMORY" ]; then
    echo "TASK_MEMORY must not be empty" >&2
    exit 1
fi
if [ -z "$PRIMARY_TASK_MEMORY" ]; then
    echo "PRIMARY_TASK_MEMORY must not be empty" >&2
    exit 1
fi
if [ -z "$RETRY_TASK_MEMORY" ]; then
    echo "RETRY_TASK_MEMORY must not be empty" >&2
    exit 1
fi
if ! [[ "$WAVE_STRIDE" =~ ^[1-9][0-9]*$ ]]; then
    echo "WAVE_STRIDE must be a positive integer" >&2
    exit 1
fi
if ! [[ "$WAVE_ATTEMPT" =~ ^[1-9][0-9]*$ ]]; then
    echo "WAVE_ATTEMPT must be a positive integer" >&2
    exit 1
fi
if ! [[ "$MAX_WAVE_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
    echo "MAX_WAVE_ATTEMPTS must be a positive integer" >&2
    exit 1
fi
if [ "$DISPATCH_MODE" = "lanes" ] && [ "$WAVE_ATTEMPT" -eq 1 ] &&
    [ -e "$CAMPAIGN_CONTROL_DIR/handoff_to_third_campaign" ]; then
    echo "Third-campaign handoff requested; wave $WAVE_INDEX was not submitted"
    exit 0
fi
export TASK_CPUS TASK_MEMORY PRIMARY_TASK_MEMORY RETRY_TASK_MEMORY

IFS=$'\t' read -r WAVE_MANIFEST WAVE_COUNT WAVE_TASK_COUNT < <(
    python3 - "$CAMPAIGN_MANIFEST" "$WAVE_INDEX" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as file:
    campaign = json.load(file)
index = int(sys.argv[2])
if not 0 <= index < campaign["wave_count"]:
    raise SystemExit(f"Wave index out of range: {index}")
wave = campaign["waves"][index]
print(
    wave["manifest"],
    campaign["wave_count"],
    wave["task_count"],
    sep="\t",
)
PY
)
WAVE_TAG=$(printf "wave_%04d" "$WAVE_INDEX")
TASK_LOG_DIR="$RUN_LOG_DIR/tasks/$WAVE_TAG"
mkdir -p "$TASK_LOG_DIR" "$PENDING_DIR/waves" "$STATUS_DIR/waves"

ARRAY_MAX=$((WAVE_TASK_COUNT - 1))
if [ "$DISPATCH_MODE" = "continuous" ]; then
    ARRAY_SPEC="0-${ARRAY_MAX}"
    echo "Submitting $WAVE_TAG: $WAVE_TASK_COUNT tasks, cluster-managed concurrency"
else
    CONCURRENCY="$MAX_CONCURRENT_TASKS"
    if [ "$WAVE_TASK_COUNT" -lt "$CONCURRENCY" ]; then
        CONCURRENCY="$WAVE_TASK_COUNT"
    fi
    ARRAY_SPEC="0-${ARRAY_MAX}%${CONCURRENCY}"
    echo "Submitting $WAVE_TAG: $WAVE_TASK_COUNT tasks, concurrency $CONCURRENCY"
fi
WRITE_PROVENANCE=0
if [ "$WAVE_INDEX" -eq 0 ]; then
    WRITE_PROVENANCE=1
fi

export SHARD_MANIFEST="$WAVE_MANIFEST" INPUT_MODE=sharded
export FRESH_RUN="$CAMPAIGN_FRESH_RUN" RUN_ROOT PENDING_DIR STATUS_DIR
export PROVENANCE_RUN_ID WRITE_PROVENANCE

echo "Attempt $WAVE_ATTEMPT/$MAX_WAVE_ATTEMPTS"
echo "Task resources: $TASK_CPUS CPUs and $TASK_MEMORY memory per task"
ARRAY_SUBMISSION=$(sbatch --parsable \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task="$TASK_CPUS" \
    --mem="$TASK_MEMORY" \
    --export=ALL \
    --output="$TASK_LOG_DIR/task_%A_%a.log" \
    --array="$ARRAY_SPEC" \
    submit_bigred.slurm)
ARRAY_JOB_ID=${ARRAY_SUBMISSION%%;*}

PENDING_RUN_ID="slurm_$ARRAY_JOB_ID"
export WAVE_MODE=1 DISPATCH_MODE WAVE_INDEX WAVE_COUNT WAVE_TASK_COUNT
export WAVE_STRIDE CAMPAIGN_LANES WAVE_ATTEMPT MAX_WAVE_ATTEMPTS
export CAMPAIGN_NAME CAMPAIGN_CONTROL_DIR CAMPAIGN_PENDING_DIR CAMPAIGN_STATUS_DIR
export CAMPAIGN_MANIFEST CAMPAIGN_FINAL_PENDING_OUTPUT CAMPAIGN_FRESH_RUN
export MAX_CONCURRENT_TASKS CONCURRENCY_FILE TASK_CPUS TASK_MEMORY
export PRIMARY_TASK_MEMORY RETRY_TASK_MEMORY TASK_LOG_DIR RUN_LOG_DIR
export ARRAY_JOB_ID PENDING_RUN_ID

FINALIZATION_SUBMISSION=$(sbatch --parsable \
    --dependency=afterany:"$ARRAY_JOB_ID" \
    --export=ALL \
    --output="$RUN_LOG_DIR/finalize_${WAVE_TAG}_%j.log" \
    finalize_run.slurm)
FINALIZATION_JOB_ID=${FINALIZATION_SUBMISSION%%;*}

python3 scripts/record_submission.py \
    --path "$CAMPAIGN_CONTROL_DIR/submissions.tsv" \
    --wave-index "$WAVE_INDEX" \
    --array-job-id "$ARRAY_JOB_ID" \
    --finalizer-job-id "$FINALIZATION_JOB_ID"

echo "Array job: $ARRAY_JOB_ID"
echo "Wave finalizer: $FINALIZATION_JOB_ID"
