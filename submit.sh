#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔎 Validating the pinned runtime and NLP resources..."
python3 scripts/validate_environment.py

ID_LIST="${ID_LIST:-htids.txt}"

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
sbatch --export=ALL,ID_LIST="$ID_LIST" --array=0-$ARRAY_MAX submit_bigred.slurm
echo "✅ Monitor with: squeue -u $USER"
