#!/bin/bash

#SBATCH --job-name=htrc_pending
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB
#SBATCH --time=00:30:00
#SBATCH --partition=general
#SBATCH --output=logs/pending_%j.out
#SBATCH --error=logs/pending_%j.err

set -euo pipefail

module load python
cd "$SLURM_SUBMIT_DIR"

: "${PENDING_DIR:?PENDING_DIR is required}"
: "${PENDING_RUN_ID:?PENDING_RUN_ID is required}"

python3 scripts/consolidate_pending.py \
    --pending-dir "$PENDING_DIR" \
    --run-id "$PENDING_RUN_ID" \
    --delete-shards
