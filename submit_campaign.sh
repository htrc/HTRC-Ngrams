#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

: "${SHARD_MANIFEST:?SHARD_MANIFEST must point to the language-shard manifest}"

SHARD_COUNTS="${SHARD_COUNTS:-$(dirname "$SHARD_MANIFEST")/shard_counts.tsv}"
RUN_ROOT="${RUN_ROOT:-outputs/current_campaign}"
WAVE_SIZE="${WAVE_SIZE:-900}"
MAX_CONCURRENT_TASKS="${MAX_CONCURRENT_TASKS:-500}"
HIGHPRIO_MAX_CONCURRENT_TASKS=500
CAMPAIGN_LANES="${CAMPAIGN_LANES:-1}"
TASK_CPUS="${TASK_CPUS:-8}"
TASK_MEMORY="${TASK_MEMORY:-16GB}"
PRIMARY_TASK_MEMORY="$TASK_MEMORY"
RETRY_TASK_MEMORY="${RETRY_TASK_MEMORY:-32GB}"
CAMPAIGN_FRESH_RUN="${FRESH_RUN:-1}"
CAMPAIGN_NAME="${CAMPAIGN_NAME:-initial}"
PROVENANCE_RUN_ID="${PROVENANCE_RUN_ID:-campaign_${CAMPAIGN_NAME}}"
MAX_WAVE_ATTEMPTS="${MAX_WAVE_ATTEMPTS:-2}"

if [[ ! "$CAMPAIGN_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "CAMPAIGN_NAME may contain only letters, numbers, dot, underscore, and hyphen" >&2
    exit 1
fi

if [ "$CAMPAIGN_FRESH_RUN" = "1" ] && [ -e "$RUN_ROOT" ]; then
    echo "Fresh campaign output already exists: $RUN_ROOT" >&2
    exit 1
fi
if [ ! -f "$SHARD_MANIFEST" ]; then
    echo "Shard manifest not found: $SHARD_MANIFEST" >&2
    exit 1
fi
if ! [[ "$MAX_CONCURRENT_TASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "MAX_CONCURRENT_TASKS must be greater than zero" >&2
    exit 1
fi
if [ "$MAX_CONCURRENT_TASKS" -gt "$HIGHPRIO_MAX_CONCURRENT_TASKS" ]; then
    echo "MAX_CONCURRENT_TASKS cannot exceed the approved highprio limit of $HIGHPRIO_MAX_CONCURRENT_TASKS" >&2
    exit 1
fi
if ! [[ "$CAMPAIGN_LANES" =~ ^[1-9][0-9]*$ ]]; then
    echo "CAMPAIGN_LANES must be greater than zero" >&2
    exit 1
fi
if [ $((MAX_CONCURRENT_TASKS * CAMPAIGN_LANES)) -gt "$HIGHPRIO_MAX_CONCURRENT_TASKS" ]; then
    echo "Combined lane concurrency cannot exceed the approved highprio limit of $HIGHPRIO_MAX_CONCURRENT_TASKS" >&2
    exit 1
fi
if [ $((WAVE_SIZE * CAMPAIGN_LANES + CAMPAIGN_LANES)) -gt 1000 ]; then
    echo "Active lane arrays and finalizers cannot exceed the 1,000-job queue limit" >&2
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
if [ -z "$RETRY_TASK_MEMORY" ]; then
    echo "RETRY_TASK_MEMORY must not be empty" >&2
    exit 1
fi
if ! [[ "$MAX_WAVE_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
    echo "MAX_WAVE_ATTEMPTS must be a positive integer" >&2
    exit 1
fi

CAMPAIGN_CONTROL_DIR="$RUN_ROOT/control/$CAMPAIGN_NAME"
CAMPAIGN_PENDING_DIR="$RUN_ROOT/pending/campaigns/$CAMPAIGN_NAME"
CAMPAIGN_STATUS_DIR="$RUN_ROOT/status/campaigns/$CAMPAIGN_NAME"
SUBMISSIONS_FILE="$CAMPAIGN_CONTROL_DIR/submissions.tsv"
if [ -e "$SUBMISSIONS_FILE" ]; then
    echo "Campaign has already been submitted: $SUBMISSIONS_FILE" >&2
    echo "Use a new CAMPAIGN_NAME, or resume individual waves with submit_wave.sh." >&2
    exit 1
fi
mkdir -p "$CAMPAIGN_CONTROL_DIR" "$RUN_ROOT/logs" \
    "$CAMPAIGN_PENDING_DIR/waves" "$CAMPAIGN_STATUS_DIR/waves" \
    "$RUN_ROOT/metadata"

WAVE_DIR="$CAMPAIGN_CONTROL_DIR/waves"
WAVE_ARGS=(
    --shard-manifest "$SHARD_MANIFEST"
    --output-dir "$WAVE_DIR"
    --wave-size "$WAVE_SIZE"
)
if [ -f "$SHARD_COUNTS" ]; then
    WAVE_ARGS+=(--shard-counts "$SHARD_COUNTS")
fi
python3 scripts/create_wave_manifests.py "${WAVE_ARGS[@]}"

CONCURRENCY_FILE="$CAMPAIGN_CONTROL_DIR/max_concurrent_tasks"
if [ ! -e "$CONCURRENCY_FILE" ]; then
    CONCURRENCY_TMP="$CONCURRENCY_FILE.$$.tmp"
    printf "%s\n" "$MAX_CONCURRENT_TASKS" > "$CONCURRENCY_TMP"
    mv "$CONCURRENCY_TMP" "$CONCURRENCY_FILE"
fi

CAMPAIGN_MANIFEST="$WAVE_DIR/campaign_manifest.json"
CAMPAIGN_FINAL_PENDING_OUTPUT="${CAMPAIGN_FINAL_PENDING_OUTPUT:-$CAMPAIGN_PENDING_DIR/pending_volumes.tsv}"
printf "wave_index\tarray_job_id\tfinalizer_job_id\n" \
    > "$SUBMISSIONS_FILE"

export RUN_ROOT WAVE_DIR CAMPAIGN_MANIFEST MAX_CONCURRENT_TASKS
export CONCURRENCY_FILE
export TASK_CPUS TASK_MEMORY PRIMARY_TASK_MEMORY RETRY_TASK_MEMORY
export CAMPAIGN_FRESH_RUN CAMPAIGN_NAME CAMPAIGN_CONTROL_DIR
export CAMPAIGN_PENDING_DIR CAMPAIGN_STATUS_DIR
export PROVENANCE_RUN_ID
export CAMPAIGN_LANES WAVE_STRIDE="$CAMPAIGN_LANES"
export MAX_WAVE_ATTEMPTS CAMPAIGN_FINAL_PENDING_OUTPUT

WAVE_COUNT=$(python3 - "$CAMPAIGN_MANIFEST" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as file:
    print(json.load(file)["wave_count"])
PY
)

INITIAL_LANES="$CAMPAIGN_LANES"
if [ "$WAVE_COUNT" -lt "$INITIAL_LANES" ]; then
    INITIAL_LANES="$WAVE_COUNT"
fi

for ((wave_index = 0; wave_index < INITIAL_LANES; wave_index++)); do
    bash submit_wave.sh "$wave_index"
done
