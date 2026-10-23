#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ID_LIST="${ID_LIST:-htids.txt}"
SHARD_MANIFEST="${SHARD_MANIFEST:-}"
RUN_ROOT="${RUN_ROOT:-outputs/current_run}"
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"
RUN_LOG_DIR="${RUN_LOG_DIR:-$RUN_ROOT/logs}"
TASK_LOG_DIR="${TASK_LOG_DIR:-$RUN_LOG_DIR/tasks}"
STATUS_DIR="${STATUS_DIR:-$RUN_ROOT/status}"
TASK_CPUS="${TASK_CPUS:-8}"
TASK_MEMORY="${TASK_MEMORY:-32GB}"

if ! [[ "$TASK_CPUS" =~ ^[1-9][0-9]*$ ]]; then
    echo "❌ TASK_CPUS must be a positive integer"
    exit 1
fi
if [ -z "$TASK_MEMORY" ]; then
    echo "❌ TASK_MEMORY must not be empty"
    exit 1
fi

if [ -n "$SHARD_MANIFEST" ]; then
    if [ ! -f "$SHARD_MANIFEST" ]; then
        echo "❌ Shard manifest not found: $SHARD_MANIFEST"
        exit 1
    fi
    N=$(awk 'NF {count++} END {print count+0}' "$SHARD_MANIFEST")
    INPUT_MODE="sharded"
    INPUT_DESCRIPTION="$N task-specific shard(s)"
    ARRAY_MAX=$((N-1))
else
    if [ ! -f "$ID_LIST" ]; then
        echo "❌ HTID list not found: $ID_LIST"
        exit 1
    fi
    N=$(awk 'NF && $0 !~ /^[[:space:]]*#/ {count++} END {print count+0}' "$ID_LIST")
    INPUT_MODE="unsharded"
    INPUT_DESCRIPTION="$N volume(s)"

    MAX_TASKS="${MAX_TASKS:-300}"
    if [ "$N" -lt "$MAX_TASKS" ]; then
        ARRAY_MAX=$((N-1))
    else
        ARRAY_MAX=$((MAX_TASKS-1))
    fi
fi

if [ "$N" -eq 0 ]; then
    echo "❌ Input is empty!"
    exit 1
fi

FRESH_RUN="${FRESH_RUN:-0}"
if [ "$FRESH_RUN" = "1" ] && [ -e "$RUN_ROOT" ]; then
    echo "❌ Fresh-run output already exists: $RUN_ROOT"
    exit 1
fi

echo "📦 Submitting $INPUT_DESCRIPTION across $((ARRAY_MAX+1)) task(s)..."
echo "🖥️  Task resources: $TASK_CPUS CPUs, $TASK_MEMORY memory"
mkdir -p "$TASK_LOG_DIR"
ARRAY_SUBMISSION=$(sbatch --parsable \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task="$TASK_CPUS" \
    --mem="$TASK_MEMORY" \
    --export=ALL,ID_LIST="$ID_LIST",SHARD_MANIFEST="$SHARD_MANIFEST",INPUT_MODE="$INPUT_MODE",FRESH_RUN="$FRESH_RUN",RUN_ROOT="$RUN_ROOT",PENDING_DIR="$PENDING_DIR",STATUS_DIR="$STATUS_DIR" \
    --output="$TASK_LOG_DIR/task_%A_%a.log" \
    --array=0-$ARRAY_MAX \
    submit_bigred.slurm)
ARRAY_JOB_ID=${ARRAY_SUBMISSION%%;*}
echo "✅ Array job: $ARRAY_JOB_ID"

FINALIZATION_SUBMISSION=$(sbatch --parsable \
    --dependency=afterany:"$ARRAY_JOB_ID" \
    --export=ALL,PENDING_DIR="$PENDING_DIR",STATUS_DIR="$STATUS_DIR",PENDING_RUN_ID="slurm_$ARRAY_JOB_ID",TASK_LOG_DIR="$TASK_LOG_DIR",RUN_LOG_DIR="$RUN_LOG_DIR",ARRAY_JOB_ID="$ARRAY_JOB_ID",WAVE_TASK_COUNT="$((ARRAY_MAX+1))" \
    --output="$RUN_LOG_DIR/finalize_%j.log" \
    finalize_run.slurm)
FINALIZATION_JOB_ID=${FINALIZATION_SUBMISSION%%;*}

echo "✅ Run finalization job: $FINALIZATION_JOB_ID"
echo "✅ Monitor with: squeue -u $USER"
