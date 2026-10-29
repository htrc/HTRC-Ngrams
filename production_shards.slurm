#!/bin/bash
#SBATCH --job-name=htrc_shards
#SBATCH -A r00124
#SBATCH --partition=general
#SBATCH --qos=highprio
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=16GB
#SBATCH --time=1-00:00:00
#SBATCH --output=logs/production_shards_%j.log

set -euo pipefail

module load python
source /N/project/htrc/venvs/htrc-ngram-stanza/bin/activate
cd /N/project/htrc/HTRC-Ngrams

mkdir -p logs

python3 scripts/create_language_shards.py \
  --input input/hathi_full_20261001_htid_lang.tsv \
  --output-dir shards/production_20261001_count_balanced \
  --target-volumes 100 \
  --checkpoint-interval 1000000 \
  --resume
