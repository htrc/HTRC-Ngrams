#!/bin/bash

#SBATCH --job-name=htrc_ngrams
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32GB
#SBATCH --time=1-00:00:00
#SBATCH --partition=general
#SBATCH --mail-type=END,FAIL
#SBATCH --output=logs/ngrams_%A_%a.log


# ---------------------------------------------------------------------------
# BigRed200 HTRC N-grams Execution Script
# ---------------------------------------------------------------------------

set -euo pipefail

echo "=========================================================="
echo "Job Start Time: $(date)"
echo "Running on node: $SLURMD_NODENAME"
echo "=========================================================="

# 1. Load the Python module and environment on BigRed200
module load python
VENV_PATH="${VENV_PATH:-/N/project/htrc/venvs/htrc-ngram-stanza}"
source "$VENV_PATH/bin/activate"
export STANZA_RESOURCES_DIR="${STANZA_RESOURCES_DIR:-/N/project/htrc/stanza_resources}"
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

# 2. Enter working directory
cd "$SLURM_SUBMIT_DIR"

# 3. Define BigRed200 Paths
# Using the dedicated HTRC storage mount point you provided: 
PAIRTREE_ROOT="${PAIRTREE_ROOT:-/N/project/htrc/data/ht_text}"
BIBFRAME_ROOT="${BIBFRAME_ROOT:-/N/project/htrc/extracted-features/data/bibframe-jsonld}"

# This text file must be in the same folder as this script, containing your IDs
ID_LIST="${ID_LIST:-htids.txt}"
RUN_ROOT="${RUN_ROOT:-outputs/current_run}"

INPUT_ARGS=()
if [ "${INPUT_MODE:-unsharded}" = "sharded" ]; then
    : "${SHARD_MANIFEST:?SHARD_MANIFEST is required for sharded input}"
    SHARD_NUMBER=$((SLURM_ARRAY_TASK_ID + 1))
    ID_LIST=$(awk -v target="$SHARD_NUMBER" 'NF {count++} count == target {print; exit}' "$SHARD_MANIFEST")
    if [ -z "$ID_LIST" ] || [ ! -f "$ID_LIST" ]; then
        echo "Unable to resolve shard $SHARD_NUMBER from $SHARD_MANIFEST" >&2
        exit 1
    fi
    INPUT_ARGS+=(--input-is-task-shard)
fi

if [ "${FRESH_RUN:-0}" = "1" ]; then
    INPUT_ARGS+=(--skip-checkpoints)
fi

OUTPUT_DIR="${OUTPUT_DIR:-$RUN_ROOT/bigrams}"
METADATA_DIR="${METADATA_DIR:-$RUN_ROOT/metadata}"
# SUMMARY_DIR="${SUMMARY_DIR:-$RUN_ROOT/summaries}"
# Summary CSV export is disabled by omitting --summary-dir below.
PENDING_DIR="${PENDING_DIR:-$RUN_ROOT/pending}"
MAX_ZIP_SIZE_MB="${MAX_ZIP_SIZE_MB:-150}"

# 4. Execute the Python workload
echo "Commencing HPC massive Pairtree reading pipeline..."

# The central orchestrator runs the modular package execution
python3 -u run_pipeline.py \
    --pairtree "$PAIRTREE_ROOT" \
    --id-list "$ID_LIST" \
    --bibframe-root "$BIBFRAME_ROOT" \
    --output-dir "$OUTPUT_DIR" \
    --metadata-dir "$METADATA_DIR" \
    --pending-dir "$PENDING_DIR" \
    --max-zip-size-mb "$MAX_ZIP_SIZE_MB" \
    "${INPUT_ARGS[@]}"

# Pairtree output is the default. Do not pass --flat-output for production.

echo "=========================================================="
echo "Job End Time: $(date)"
echo "=========================================================="
