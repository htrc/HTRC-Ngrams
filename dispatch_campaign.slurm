#!/bin/bash

#SBATCH --job-name=htrc_dispatch
#SBATCH -A r00124
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1GB
#SBATCH --time=1-00:00:00
#SBATCH --partition=general
#SBATCH --qos=highprio
#SBATCH --mail-type=FAIL,TIME_LIMIT

set -euo pipefail

module load python
cd "$SLURM_SUBMIT_DIR"

python3 -u scripts/dispatch_campaign.py --duration-seconds 82800
