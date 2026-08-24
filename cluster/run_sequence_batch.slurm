#!/usr/bin/env bash
#SBATCH --job-name=of_sequence_batch
#SBATCH --partition=oldcpu
#SBATCH --gres=gpu:nvidia_rtx_a6000:1
#SBATCH --time=02:00:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --output=logs/sequence_batch_%j.out
#SBATCH --error=logs/sequence_batch_%j.err

set -euo pipefail

: "${INPUT_DIR:?Set INPUT_DIR with sbatch --export}"
: "${OUTPUT_DIR:?Set OUTPUT_DIR with sbatch --export}"
: "${PAIR_START:?Set PAIR_START with sbatch --export}"
: "${PAIR_END:?Set PAIR_END with sbatch --export}"

PROJECT_DIR="${SLURM_SUBMIT_DIR:?Submit this job from the project root}"
FRAME_PATTERN="${FRAME_PATTERN:-}"
if [[ -z "$FRAME_PATTERN" ]]; then
  FRAME_PATTERN='Frame_{index:04d}.tif'
fi
FLOW_DTYPE="${FLOW_DTYPE:-float16}"
OVERLAY_EVERY="${OVERLAY_EVERY:-0}"
GRID_STEP="${GRID_STEP:-24}"

cd "$PROJECT_DIR"
mkdir -p logs "$OUTPUT_DIR"

echo "job_id=${SLURM_JOB_ID}"
echo "node=$(hostname)"
echo "pairs=${PAIR_START}-${PAIR_END}"
echo "flow_dtype=${FLOW_DTYPE}"
echo "frame_pattern=${FRAME_PATTERN}"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

nix-shell shell.nix --run "
  bash cluster/setup_env.sh
  .venv/bin/python scripts/analyze_sequence.py \
    '$INPUT_DIR' \
    --pairs '$PAIR_START-$PAIR_END' \
    --pattern '$FRAME_PATTERN' \
    --output-dir '$OUTPUT_DIR' \
    --delta-t-s 0.5 \
    --device cuda \
    --flow-dtype '$FLOW_DTYPE' \
    --overlay-every '$OVERLAY_EVERY' \
    --grid-step '$GRID_STEP'
"
