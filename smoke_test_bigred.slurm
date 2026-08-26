#!/bin/bash

#SBATCH --job-name=htrc_smoke_test
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16GB
#SBATCH --time=00:30:00
#SBATCH --partition=general
#SBATCH --output=logs/smoke_test_%j.out
#SBATCH --error=logs/smoke_test_%j.err

set -euo pipefail

echo "=========================================================="
echo "Job Start Time: $(date)"
echo "Running on node: $SLURMD_NODENAME"
echo "=========================================================="

module load python
source /N/project/htrc/venvs/htrc-ngram-stanza/bin/activate
export STANZA_RESOURCES_DIR=/N/project/htrc/stanza_resources
export NLTK_DATA=/N/project/htrc/nltk_data

cd $SLURM_SUBMIT_DIR

echo "Running 27-language Stanza tokenization validation..."
python3 scripts/validate_stanza_languages.py \
    --resource-dir "$STANZA_RESOURCES_DIR" \
    --output "test_outputs/stanza_27_language_validation_bigred_${SLURM_JOB_ID}.tsv"

echo "Smoke test completed."
