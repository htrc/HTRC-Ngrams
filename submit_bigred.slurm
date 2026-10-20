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
#SBATCH --output=logs/ngrams_%A_%a.out
#SBATCH --error=logs/ngrams_%A_%a.err


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
export NLTK_DATA="${NLTK_DATA:-/N/project/htrc/nltk_data}"

# 2. Enter working directory
cd "$SLURM_SUBMIT_DIR"

# 3. Define BigRed200 Paths
# Using the dedicated HTRC storage mount point you provided: 
PAIRTREE_ROOT="${PAIRTREE_ROOT:-/N/project/htrc/data/ht_text}"
BIBFRAME_ROOT="${BIBFRAME_ROOT:-/N/project/htrc/extracted-features/data/bibframe-jsonld}"

# This text file must be in the same folder as this script, containing your IDs
ID_LIST="${ID_LIST:-htids.txt}"
RUN_ROOT="${RUN_ROOT:-outputs/current_run}"

OUTPUT_DIR="${OUTPUT_DIR:-$RUN_ROOT/bigrams}"
AUDIT_DIR="${AUDIT_DIR:-$RUN_ROOT/audit}"
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
    --audit-dir "$AUDIT_DIR" \
    --metadata-dir "$METADATA_DIR" \
    --pending-dir "$PENDING_DIR" \
    --max-zip-size-mb "$MAX_ZIP_SIZE_MB" \
    --min-freq 2 \
    --min-pmi 0.0 \
    --max-exposure 0.15 \
    --no-filter1 \
    --no-filter2 \
    --no-filter3

# Pairtree output is the default. Do not pass --flat-output for production.

echo "=========================================================="
echo "Job End Time: $(date)"
echo "=========================================================="
