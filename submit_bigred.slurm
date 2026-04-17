#!/bin/bash

#SBATCH --job-name=htrc_ngrams
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32GB
#SBATCH --time=04:00:00
#SBATCH --partition=general
#SBATCH --mail-type=END,FAIL
#SBATCH --output=ngrams_%j.out
#SBATCH --error=ngrams_%j.err

# ---------------------------------------------------------------------------
# BigRed200 HTRC N-grams Execution Script
# ---------------------------------------------------------------------------

echo "=========================================================="
echo "Job Start Time: $(date)"
echo "Running on node: $SLURMD_NODENAME"
echo "=========================================================="

# 1. Load required Python module on BigRed200
# Note: You may need to replace this with the exact python module string (e.g. module load python/3.10.x)
module load python

# 2. Enter working directory
cd $SLURM_SUBMIT_DIR

# 3. Define BigRed200 Paths
# Using the dedicated HTRC storage mount point you provided: 
PAIRTREE_ROOT="/N/project/htrc/data/ht_text"

# This text file must be in the same folder as this script, containing your IDs
ID_LIST="htids.txt"

# 4. Execute the Python workload
echo "Commencing HPC massive Pairtree reading pipeline..."

# The central orchestrator runs the modular package execution
python3 run_pipeline.py \
    --pairtree "$PAIRTREE_ROOT" \
    --id-list "$ID_LIST" \
    --min-freq 2 \
    --min-pmi 0.0 \
    --max-exposure 0.15

echo "=========================================================="
echo "Job End Time: $(date)"
echo "=========================================================="
