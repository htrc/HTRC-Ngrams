#!/bin/bash

#SBATCH --job-name=htrc_finalize
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB
#SBATCH --time=00:30:00
#SBATCH --partition=general

set -euo pipefail

module load python
cd "$SLURM_SUBMIT_DIR"

: "${PENDING_DIR:?PENDING_DIR is required}"
: "${PENDING_RUN_ID:?PENDING_RUN_ID is required}"
: "${TASK_LOG_DIR:?TASK_LOG_DIR is required}"
: "${RUN_LOG_DIR:?RUN_LOG_DIR is required}"
: "${ARRAY_JOB_ID:?ARRAY_JOB_ID is required}"

finalization_status=0

if ! python3 scripts/consolidate_logs.py \
    --task-log-dir "$TASK_LOG_DIR" \
    --output "$RUN_LOG_DIR/slurm_${ARRAY_JOB_ID}.log" \
    --job-id "$ARRAY_JOB_ID" \
    --delete-task-logs; then
    echo "Task-log consolidation failed for array $ARRAY_JOB_ID" >&2
    finalization_status=1
fi

if ! python3 scripts/consolidate_pending.py \
    --pending-dir "$PENDING_DIR" \
    --run-id "$PENDING_RUN_ID" \
    --delete-shards; then
    echo "Pending-volume consolidation failed for array $ARRAY_JOB_ID" >&2
    finalization_status=1
fi

exit "$finalization_status"
