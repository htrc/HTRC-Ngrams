#!/bin/bash

#SBATCH --job-name=htrc_finalize
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB
#SBATCH --time=00:30:00
#SBATCH --partition=general
#SBATCH --qos=highprio
#SBATCH --mail-type=FAIL

set -euo pipefail

module load python
cd "$SLURM_SUBMIT_DIR"

: "${PENDING_DIR:?PENDING_DIR is required}"
: "${PENDING_RUN_ID:?PENDING_RUN_ID is required}"
: "${TASK_LOG_DIR:?TASK_LOG_DIR is required}"
: "${RUN_LOG_DIR:?RUN_LOG_DIR is required}"
: "${ARRAY_JOB_ID:?ARRAY_JOB_ID is required}"
: "${STATUS_DIR:?STATUS_DIR is required}"
: "${WAVE_TASK_COUNT:?WAVE_TASK_COUNT is required}"

finalization_status=0
WAVE_MODE="${WAVE_MODE:-0}"
WAVE_STRIDE="${WAVE_STRIDE:-1}"
WAVE_ATTEMPT="${WAVE_ATTEMPT:-1}"
MAX_WAVE_ATTEMPTS="${MAX_WAVE_ATTEMPTS:-1}"
PRIMARY_TASK_MEMORY="${PRIMARY_TASK_MEMORY:-${TASK_MEMORY:-16GB}}"
RETRY_TASK_MEMORY="${RETRY_TASK_MEMORY:-32GB}"

if [ "$WAVE_MODE" = "1" ]; then
    : "${WAVE_INDEX:?WAVE_INDEX is required}"
    : "${WAVE_COUNT:?WAVE_COUNT is required}"
    : "${CAMPAIGN_MANIFEST:?CAMPAIGN_MANIFEST is required}"
    : "${CAMPAIGN_PENDING_DIR:?CAMPAIGN_PENDING_DIR is required}"
    : "${CAMPAIGN_STATUS_DIR:?CAMPAIGN_STATUS_DIR is required}"
    : "${RUN_ROOT:?RUN_ROOT is required}"
    WAVE_TAG=$(printf "wave_%04d" "$WAVE_INDEX")
    LOG_OUTPUT="$RUN_LOG_DIR/${WAVE_TAG}_slurm_${ARRAY_JOB_ID}.log"
    PENDING_OUTPUT="$CAMPAIGN_PENDING_DIR/waves/${WAVE_TAG}.tsv"
    STATUS_OUTPUT="$CAMPAIGN_STATUS_DIR/waves/${WAVE_TAG}.json"
else
    LOG_OUTPUT="$RUN_LOG_DIR/slurm_${ARRAY_JOB_ID}.log"
    PENDING_OUTPUT="$PENDING_DIR/pending_volumes.tsv"
    STATUS_OUTPUT="$STATUS_DIR/slurm_${ARRAY_JOB_ID}.json"
fi

if ! python3 scripts/consolidate_logs.py \
    --task-log-dir "$TASK_LOG_DIR" \
    --output "$LOG_OUTPUT" \
    --job-id "$ARRAY_JOB_ID" \
    --expected-logs "$WAVE_TASK_COUNT" \
    --delete-task-logs; then
    echo "Task-log consolidation failed for array $ARRAY_JOB_ID" >&2
    finalization_status=1
fi

if ! python3 scripts/consolidate_pending.py \
    --pending-dir "$PENDING_DIR" \
    --run-id "$PENDING_RUN_ID" \
    --output "$PENDING_OUTPUT" \
    --expected-shards "$WAVE_TASK_COUNT" \
    --delete-shards; then
    echo "Pending-volume consolidation failed for array $ARRAY_JOB_ID" >&2
    finalization_status=1
fi

if ! python3 scripts/consolidate_status.py \
    --status-dir "$STATUS_DIR" \
    --run-id "$PENDING_RUN_ID" \
    --expected-tasks "$WAVE_TASK_COUNT" \
    --output "$STATUS_OUTPUT" \
    --delete-shards; then
    echo "Task-status consolidation failed for array $ARRAY_JOB_ID" >&2
    finalization_status=1
fi

if [ "$finalization_status" -ne 0 ]; then
    if [ "$WAVE_MODE" = "1" ] && [ "$WAVE_ATTEMPT" -lt "$MAX_WAVE_ATTEMPTS" ]; then
        NEXT_ATTEMPT=$((WAVE_ATTEMPT + 1))
        echo "Finalization failed; retrying $WAVE_TAG with checkpoints (attempt $NEXT_ATTEMPT/$MAX_WAVE_ATTEMPTS)." >&2
        export CAMPAIGN_FRESH_RUN=0
        export WAVE_ATTEMPT="$NEXT_ATTEMPT"
        export TASK_MEMORY="$RETRY_TASK_MEMORY"
        echo "Retry task memory: $TASK_MEMORY" >&2
        bash submit_wave.sh "$WAVE_INDEX"
        exit 0
    fi
    echo "Finalization failed after $WAVE_ATTEMPT attempt(s); no later wave in this lane was submitted." >&2
    exit "$finalization_status"
fi

if [ "$WAVE_MODE" = "1" ]; then
    NEXT_WAVE=$((WAVE_INDEX + WAVE_STRIDE))
    if [ "$NEXT_WAVE" -lt "$WAVE_COUNT" ]; then
        export WAVE_ATTEMPT=1
        export TASK_MEMORY="$PRIMARY_TASK_MEMORY"
        bash submit_wave.sh "$NEXT_WAVE"
    else
        python3 scripts/finalize_campaign_if_ready.py \
            --campaign-manifest "$CAMPAIGN_MANIFEST" \
            --pending-dir "$CAMPAIGN_PENDING_DIR" \
            --status-dir "$CAMPAIGN_STATUS_DIR" \
            --final-pending-output "$CAMPAIGN_FINAL_PENDING_OUTPUT"
    fi
fi

exit "$finalization_status"
