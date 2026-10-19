#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ID_LIST="${ID_LIST:-htids.txt}"
RUN_ROOT="${RUN_ROOT:-runs/current_run}"
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"

if [ ! -f "$ID_LIST" ]; then
    echo "❌ HTID list not found: $ID_LIST"
    exit 1
fi

N=$(awk 'NF && $0 !~ /^[[:space:]]*#/ {count++} END {print count+0}' "$ID_LIST")

if [ "$N" -eq 0 ]; then
    echo "❌ $ID_LIST is empty!"
    exit 1
fi

# Cap array tasks at 1000 to prevent SLURM rejection on massive runs
MAX_TASKS=1000
if [ "$N" -lt "$MAX_TASKS" ]; then
    ARRAY_MAX=$((N-1))
else
    ARRAY_MAX=$((MAX_TASKS-1))
fi

echo "📦 Submitting $N volume(s) across $((ARRAY_MAX+1)) task(s)..."
mkdir -p logs
ARRAY_SUBMISSION=$(sbatch --parsable \
    --export=ALL,ID_LIST="$ID_LIST",RUN_ROOT="$RUN_ROOT",PENDING_DIR="$PENDING_DIR" \
    --array=0-$ARRAY_MAX \
    submit_bigred.slurm)
ARRAY_JOB_ID=${ARRAY_SUBMISSION%%;*}
echo "✅ Array job: $ARRAY_JOB_ID"

CONSOLIDATION_SUBMISSION=$(sbatch --parsable \
    --dependency=afterok:"$ARRAY_JOB_ID" \
    --export=ALL,PENDING_DIR="$PENDING_DIR",PENDING_RUN_ID="slurm_$ARRAY_JOB_ID" \
    consolidate_pending.slurm)
CONSOLIDATION_JOB_ID=${CONSOLIDATION_SUBMISSION%%;*}

echo "✅ Pending consolidation job: $CONSOLIDATION_JOB_ID"
echo "✅ Monitor with: squeue -u $USER"
