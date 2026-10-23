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

MAX_CONCURRENT_TASKS="${MAX_CONCURRENT_TASKS:-200}"
TASK_CPUS="${TASK_CPUS:-8}"
TASK_MEMORY="${TASK_MEMORY:-32GB}"
CAMPAIGN_FRESH_RUN="${CAMPAIGN_FRESH_RUN:-0}"
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"
STATUS_DIR="${STATUS_DIR:-$RUN_ROOT/status}"
RUN_LOG_DIR="${RUN_LOG_DIR:-$RUN_ROOT/logs}"

if ! [[ "$TASK_CPUS" =~ ^[1-9][0-9]*$ ]]; then
    echo "TASK_CPUS must be a positive integer" >&2
    exit 1
fi
if [ -z "$TASK_MEMORY" ]; then
    echo "TASK_MEMORY must not be empty" >&2
    exit 1
fi
export TASK_CPUS TASK_MEMORY

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
CONCURRENCY="$MAX_CONCURRENT_TASKS"
if [ "$WAVE_TASK_COUNT" -lt "$CONCURRENCY" ]; then
    CONCURRENCY="$WAVE_TASK_COUNT"
fi
ARRAY_SPEC="0-${ARRAY_MAX}%${CONCURRENCY}"
WRITE_PROVENANCE=0
if [ "$WAVE_INDEX" -eq 0 ]; then
    WRITE_PROVENANCE=1
fi

echo "Submitting $WAVE_TAG: $WAVE_TASK_COUNT tasks, concurrency $CONCURRENCY"
echo "Task resources: $TASK_CPUS CPUs, $TASK_MEMORY memory, one node per task"
ARRAY_SUBMISSION=$(sbatch --parsable \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task="$TASK_CPUS" \
    --mem="$TASK_MEMORY" \
    --export=ALL,SHARD_MANIFEST="$WAVE_MANIFEST",INPUT_MODE=sharded,FRESH_RUN="$CAMPAIGN_FRESH_RUN",RUN_ROOT="$RUN_ROOT",PENDING_DIR="$PENDING_DIR",STATUS_DIR="$STATUS_DIR",PROVENANCE_RUN_ID="$PROVENANCE_RUN_ID",WRITE_PROVENANCE="$WRITE_PROVENANCE" \
    --output="$TASK_LOG_DIR/task_%A_%a.log" \
    --array="$ARRAY_SPEC" \
    submit_bigred.slurm)
ARRAY_JOB_ID=${ARRAY_SUBMISSION%%;*}

FINALIZATION_SUBMISSION=$(sbatch --parsable \
    --dependency=afterany:"$ARRAY_JOB_ID" \
    --export=ALL,WAVE_MODE=1,WAVE_INDEX="$WAVE_INDEX",WAVE_COUNT="$WAVE_COUNT",WAVE_TASK_COUNT="$WAVE_TASK_COUNT",CAMPAIGN_NAME="$CAMPAIGN_NAME",CAMPAIGN_CONTROL_DIR="$CAMPAIGN_CONTROL_DIR",CAMPAIGN_PENDING_DIR="$CAMPAIGN_PENDING_DIR",CAMPAIGN_STATUS_DIR="$CAMPAIGN_STATUS_DIR",CAMPAIGN_MANIFEST="$CAMPAIGN_MANIFEST",CAMPAIGN_FRESH_RUN="$CAMPAIGN_FRESH_RUN",MAX_CONCURRENT_TASKS="$MAX_CONCURRENT_TASKS",TASK_CPUS="$TASK_CPUS",TASK_MEMORY="$TASK_MEMORY",PROVENANCE_RUN_ID="$PROVENANCE_RUN_ID",RUN_ROOT="$RUN_ROOT",PENDING_DIR="$PENDING_DIR",STATUS_DIR="$STATUS_DIR",PENDING_RUN_ID="slurm_$ARRAY_JOB_ID",TASK_LOG_DIR="$TASK_LOG_DIR",RUN_LOG_DIR="$RUN_LOG_DIR",ARRAY_JOB_ID="$ARRAY_JOB_ID" \
    --output="$RUN_LOG_DIR/finalize_${WAVE_TAG}_%j.log" \
    finalize_run.slurm)
FINALIZATION_JOB_ID=${FINALIZATION_SUBMISSION%%;*}

printf "%s\t%s\t%s\n" "$WAVE_INDEX" "$ARRAY_JOB_ID" "$FINALIZATION_JOB_ID" \
    >> "$CAMPAIGN_CONTROL_DIR/submissions.tsv"

echo "Array job: $ARRAY_JOB_ID"
echo "Wave finalizer: $FINALIZATION_JOB_ID"
