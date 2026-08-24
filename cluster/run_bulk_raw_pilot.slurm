#!/usr/bin/env bash
#SBATCH --job-name=of_bulk_raw_pilot
#SBATCH --partition=oldcpu
#SBATCH --gres=gpu:nvidia_rtx_a6000:1
#SBATCH --time=00:30:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --output=logs/bulk_raw_pilot_%j.out
#SBATCH --error=logs/bulk_raw_pilot_%j.err

set -euo pipefail

PROJECT_DIR="${SLURM_SUBMIT_DIR:?Submit this job from the project root}"
cd "$PROJECT_DIR"
mkdir -p logs results/Bulk_1_12_11/raw_pilot

echo "job_id=${SLURM_JOB_ID}"
echo "node=$(hostname)"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

nix-shell shell.nix --run '
  bash cluster/setup_env.sh
  .venv/bin/python scripts/analyze_sequence.py \
    data/Bulk_1_12_11/raw_pilot_frames \
    --pairs 1-12 \
    --output-dir results/Bulk_1_12_11/raw_pilot \
    --delta-t-s 0.5 \
    --device cuda
'
