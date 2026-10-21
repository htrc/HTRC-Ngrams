#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ID_LIST="${ID_LIST:-htids.txt}"
RUN_ROOT="${RUN_ROOT:-outputs/current_run}"
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"
RUN_LOG_DIR="${RUN_LOG_DIR:-$RUN_ROOT/logs}"
TASK_LOG_DIR="${TASK_LOG_DIR:-$RUN_LOG_DIR/tasks}"

if [ ! -f "$ID_LIST" ]; then
    echo "❌ HTID list not found: $ID_LIST"
    exit 1
fi

N=$(awk 'NF && $0 !~ /^[[:space:]]*#/ {count++} END {print count+0}' "$ID_LIST")

if [ "$N" -eq 0 ]; then
    echo "❌ $ID_LIST is empty!"
    exit 1
fi

# Use 300 array tasks for the current throughput test. Override with the
# MAX_TASKS environment variable for later runs.
MAX_TASKS="${MAX_TASKS:-300}"
if [ "$N" -lt "$MAX_TASKS" ]; then
    ARRAY_MAX=$((N-1))
else
    ARRAY_MAX=$((MAX_TASKS-1))
fi

echo "📦 Submitting $N volume(s) across $((ARRAY_MAX+1)) task(s)..."
mkdir -p "$TASK_LOG_DIR"
ARRAY_SUBMISSION=$(sbatch --parsable \
    --export=ALL,ID_LIST="$ID_LIST",RUN_ROOT="$RUN_ROOT",PENDING_DIR="$PENDING_DIR" \
    --output="$TASK_LOG_DIR/task_%A_%a.log" \
    --array=0-$ARRAY_MAX \
    submit_bigred.slurm)
ARRAY_JOB_ID=${ARRAY_SUBMISSION%%;*}
echo "✅ Array job: $ARRAY_JOB_ID"

FINALIZATION_SUBMISSION=$(sbatch --parsable \
    --dependency=afterany:"$ARRAY_JOB_ID" \
    --export=ALL,PENDING_DIR="$PENDING_DIR",PENDING_RUN_ID="slurm_$ARRAY_JOB_ID",TASK_LOG_DIR="$TASK_LOG_DIR",RUN_LOG_DIR="$RUN_LOG_DIR",ARRAY_JOB_ID="$ARRAY_JOB_ID" \
    --output="$RUN_LOG_DIR/finalize_%j.log" \
    finalize_run.slurm)
FINALIZATION_JOB_ID=${FINALIZATION_SUBMISSION%%;*}

echo "✅ Run finalization job: $FINALIZATION_JOB_ID"
echo "✅ Monitor with: squeue -u $USER"
