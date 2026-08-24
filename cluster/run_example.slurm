#!/usr/bin/env bash
#SBATCH --job-name=of_raft_example
#SBATCH --partition=oldcpu
#SBATCH --gres=gpu:nvidia_rtx_a6000:1
#SBATCH --time=00:30:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --output=logs/example_%j.out
#SBATCH --error=logs/example_%j.err

set -euo pipefail

PROJECT_DIR="${SLURM_SUBMIT_DIR:?Submit this job from the project root}"
cd "$PROJECT_DIR"
mkdir -p logs results/example

echo "job_id=${SLURM_JOB_ID}"
echo "node=$(hostname)"
echo "project=$PROJECT_DIR"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

nix-shell shell.nix --run '
  bash cluster/setup_env.sh
  .venv/bin/python scripts/analyze_pair.py \
    example/data/img_000000000_Red_000.tif \
    example/data/img_000000001_Red_000.tif \
    --output-dir results/example \
    --device cuda
'
