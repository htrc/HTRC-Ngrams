#!/bin/bash
N=$(grep -vc '^\s*$\|^#' htids.txt)

if [ "$N" -eq 0 ]; then
    echo "❌ htids.txt is empty!"
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
sbatch --array=0-$ARRAY_MAX submit_bigred.slurm
echo "✅ Monitor with: squeue -u $USER"
