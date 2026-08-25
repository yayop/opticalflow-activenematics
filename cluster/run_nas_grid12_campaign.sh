#!/usr/bin/env bash
set -euo pipefail

if (( $# != 5 )); then
  echo "Usage: $0 DATASET_NAME NAS_DATASET_ROOT FRAME_COUNT HEIGHT WIDTH" >&2
  exit 2
fi

DATASET_NAME="$1"
NAS_DATASET_ROOT="$2"
FRAME_COUNT="$3"
FRAME_HEIGHT="$4"
FRAME_WIDTH="$5"
NAS_HOST="${NAS_HOST:-ACTNEM}"
NAS_RESULT_NAME="${NAS_RESULT_NAME:-OpticalFlow_RAFT_grid12}"
PAIRS_PER_BATCH="${PAIRS_PER_BATCH:-1500}"
PROJECT_DIR="${PROJECT_DIR:-/home/erosas/projects/opticalflow-activenematics}"
LOCAL_INPUT_ROOT="$PROJECT_DIR/data/nas_staging/$DATASET_NAME"
LOCAL_OUTPUT_ROOT="$PROJECT_DIR/results/nas_staging/$DATASET_NAME"
STATE_ROOT="$PROJECT_DIR/results/nas_campaign_state/$DATASET_NAME"
NAS_SEQUENCE="$NAS_DATASET_ROOT/ImageSequence"
NAS_RESULT="$NAS_DATASET_ROOT/$NAS_RESULT_NAME"

for number in "$FRAME_COUNT" "$FRAME_HEIGHT" "$FRAME_WIDTH" "$PAIRS_PER_BATCH"; do
  [[ "$number" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid positive integer: $number" >&2; exit 2; }
done
[[ "$DATASET_NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Unsafe dataset name: $DATASET_NAME" >&2; exit 2; }
[[ "$NAS_RESULT_NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Unsafe NAS result name: $NAS_RESULT_NAME" >&2; exit 2; }
[[ "$NAS_DATASET_ROOT" == /volume1/homes/Edgardo\ Rosas/Bulk\ Active\ Nematics\ Videos/* ]] || {
  echo "NAS dataset is outside the allowed root: $NAS_DATASET_ROOT" >&2
  exit 2
}

remote_quote() { printf '%q' "$1"; }

remote_exists() {
  local quoted
  quoted="$(remote_quote "$1")"
  ssh -o BatchMode=yes "$NAS_HOST" "test -f $quoted"
}

remote_mkdir() {
  local quoted
  quoted="$(remote_quote "$1")"
  ssh -o BatchMode=yes "$NAS_HOST" "mkdir -p -- $quoted"
}

remote_mark_complete() {
  local batch_root="$1" quoted_source quoted_target
  quoted_source="$(remote_quote "$batch_root/verification.json")"
  quoted_target="$(remote_quote "$batch_root/_COMPLETE.json")"
  ssh -o BatchMode=yes "$NAS_HOST" "cp -- $quoted_source $quoted_target"
}

wait_for_job() {
  local job_id="$1" state=""
  while true; do
    state="$(sacct -j "$job_id" --format=State -n -X | head -n 1 | xargs || true)"
    state="${state%%+}"
    case "$state" in
      COMPLETED) return 0 ;;
      PENDING|RUNNING|CONFIGURING|COMPLETING|"") sleep 30 ;;
      *) echo "Slurm job $job_id ended in state $state" >&2; return 1 ;;
    esac
  done
}

cd "$PROJECT_DIR"
mkdir -p "$LOCAL_INPUT_ROOT" "$LOCAL_OUTPUT_ROOT" "$STATE_ROOT" logs
remote_mkdir "$NAS_RESULT"

pair_start=1
last_pair=$((FRAME_COUNT - 1))
while (( pair_start <= last_pair )); do
  pair_end=$((pair_start + PAIRS_PER_BATCH - 1))
  (( pair_end > last_pair )) && pair_end="$last_pair"
  batch_name="$(printf 'batch_%04d_%04d' "$pair_start" "$pair_end")"
  local_input="$LOCAL_INPUT_ROOT/$batch_name"
  local_output="$LOCAL_OUTPUT_ROOT/$batch_name"
  state_dir="$STATE_ROOT/$batch_name"
  nas_batch="$NAS_RESULT/$batch_name"
  complete_marker="$nas_batch/_COMPLETE.json"

  if remote_exists "$complete_marker"; then
    echo "NAS batch already complete: $DATASET_NAME/$batch_name"
    pair_start=$((pair_end + 1))
    continue
  fi

  rm -rf -- "$local_input" "$local_output"
  mkdir -p "$local_input" "$local_output" "$state_dir"
  frame_list="$state_dir/frames.txt"
  : > "$frame_list"
  for ((frame=pair_start; frame<=pair_end + 1; frame++)); do
    printf 'Frame_%04d.tif\n' "$frame" >> "$frame_list"
  done

  echo "Transferring NAS input: $DATASET_NAME/$batch_name"
  rsync -a --protect-args --files-from="$frame_list" \
    "$NAS_HOST:$NAS_SEQUENCE/" "$local_input/"

  job_name="g12_$(printf '%04d_%04d' "$pair_start" "$pair_end")"
  export_spec="ALL,INPUT_DIR=$local_input,OUTPUT_DIR=$local_output,PAIR_START=$pair_start,PAIR_END=$pair_end,FRAME_PATTERN=Frame_{index:04d}.tif,FLOW_DTYPE=float16,OVERLAY_EVERY=0,GRID_STEP=12,STORAGE_GRID_STEP=12"
  job_id="$(sbatch --parsable --job-name="$job_name" --export="$export_spec" cluster/run_sequence_batch.slurm)"
  echo "Submitted $job_id: $DATASET_NAME pairs $pair_start-$pair_end"
  printf '%s\n' "$job_id" > "$state_dir/job_id.txt"
  wait_for_job "$job_id"

  nix-shell shell.nix --run \
    ".venv/bin/python scripts/verify_sequence_batch.py '$local_output' \
      --pair-start '$pair_start' --pair-end '$pair_end' \
      --dtype float16 --height '$FRAME_HEIGHT' --width '$FRAME_WIDTH' \
      --storage-grid-step 12"
  cp "$local_output/verification.json" "$state_dir/verification.json"
  cp "$local_output/summary.csv" "$state_dir/summary.csv"

  echo "Syncing verified output to NAS: $DATASET_NAME/$batch_name"
  remote_mkdir "$nas_batch"
  rsync -a --checksum --protect-args "$local_output/" "$NAS_HOST:$nas_batch/"
  differences="$(rsync -acni --protect-args "$local_output/" "$NAS_HOST:$nas_batch/")"
  if [[ -n "$differences" ]]; then
    echo "NAS checksum verification failed for $DATASET_NAME/$batch_name" >&2
    printf '%s\n' "$differences" >&2
    exit 1
  fi
  remote_mark_complete "$nas_batch"

  rm -rf -- "$local_input" "$local_output"
  echo "Completed, verified and cleaned: $DATASET_NAME/$batch_name"
  pair_start=$((pair_end + 1))
done

manifest="$STATE_ROOT/campaign_manifest.json"
python - "$manifest" "$DATASET_NAME" "$NAS_SEQUENCE" "$NAS_RESULT" "$FRAME_COUNT" "$FRAME_HEIGHT" "$FRAME_WIDTH" <<'PY'
import json, pathlib, sys
output, name, source, result, frames, height, width = sys.argv[1:]
root = pathlib.Path(output).parent
reports = [json.loads(path.read_text()) for path in sorted(root.glob("batch_*/verification.json"))]
manifest = {
    "dataset": name,
    "source": source,
    "result": result,
    "frame_count": int(frames),
    "pair_count": int(frames) - 1,
    "full_image_shape": [int(height), int(width)],
    "stored_flow_shape": reports[0]["shape"],
    "storage_grid_step_px": 12,
    "storage_grid_origin_xy_px": [0, 0],
    "flow_dtype": "float16",
    "complete": sum(item["flow_count"] for item in reports) == int(frames) - 1,
    "batches": reports,
}
pathlib.Path(output).write_text(json.dumps(manifest, indent=2) + "\n")
PY
rsync -a --checksum --protect-args "$manifest" "$NAS_HOST:$NAS_RESULT/campaign_manifest.json"
echo "Dataset complete on NAS: $DATASET_NAME"
