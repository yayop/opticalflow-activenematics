"""Run RAFT once over selected consecutive pairs from a TIFF sequence."""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from analyze_pair import git_provenance, infer_flow, load_image, load_model, select_device


def parse_pair_spec(value: str) -> list[int]:
    indices: set[int] = set()
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) == 1:
            indices.add(int(bounds[0]))
        elif len(bounds) == 2:
            start, end = map(int, bounds)
            if end < start:
                raise argparse.ArgumentTypeError(f"Invalid descending range: {part}")
            indices.update(range(start, end + 1))
        else:
            raise argparse.ArgumentTypeError(f"Invalid pair specification: {part}")
    if not indices or min(indices) < 1:
        raise argparse.ArgumentTypeError("Pair indices must be positive.")
    return sorted(indices)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze selected adjacent TIFF pairs with one RAFT model load.")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--pairs", type=parse_pair_spec, default=parse_pair_spec("1-12"))
    parser.add_argument("--pattern", default="Frame_{index:04d}.tif")
    parser.add_argument("--model", type=Path, default=Path("models/weights.pth"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--grid-step", type=int, default=24)
    parser.add_argument("--quiver-scale", type=float, default=0.5)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--mixed-precision", action="store_true")
    parser.add_argument("--input-scale", choices=("upstream", "raft"), default="upstream")
    parser.add_argument("--delta-t-s", type=float, default=0.5)
    return parser.parse_args()


def frame_path(args: argparse.Namespace, index: int) -> Path:
    return args.input_dir / args.pattern.format(index=index)


def save_overlay(
    path: Path,
    background: np.ndarray,
    flow: np.ndarray,
    first_index: int,
    second_index: int,
    grid_step: int,
    quiver_scale: float,
) -> None:
    vx, vy = flow[..., 0], flow[..., 1]
    y, x = np.mgrid[0 : vx.shape[0] : grid_step, 0 : vx.shape[1] : grid_step]
    fig, ax = plt.subplots(figsize=(8, 11))
    ax.imshow(background, cmap="gray", vmin=0, vmax=1)
    ax.quiver(
        x,
        y,
        vx[::grid_step, ::grid_step],
        vy[::grid_step, ::grid_step],
        color="#ff2d2d",
        scale_units="xy",
        angles="xy",
        scale=quiver_scale,
        width=0.0013,
    )
    ax.set_title(f"RAFT: Frame {first_index:04d} → {second_index:04d} (px/frame)")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    if args.delta_t_s <= 0 or args.iterations < 1 or args.grid_step < 1:
        raise ValueError("Time interval, iterations and grid step must be positive.")

    output_dir = args.output_dir.resolve()
    overlays_dir = output_dir / "overlays"
    flows_dir = output_dir / "flows"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    flows_dir.mkdir(parents=True, exist_ok=True)

    device = select_device(args.device)
    model_start = time.perf_counter()
    model = load_model(args.model, device, args.mixed_precision)
    model_loading_s = time.perf_counter() - model_start
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    rows: list[dict[str, float | int | str]] = []
    run_start = time.perf_counter()
    for pair_index in args.pairs:
        first_path = frame_path(args, pair_index)
        second_path = frame_path(args, pair_index + 1)
        pair_start = time.perf_counter()
        image1, background = load_image(first_path, args.input_scale)
        image2, _ = load_image(second_path, args.input_scale)
        load_s = time.perf_counter() - pair_start

        inference_start = time.perf_counter()
        flow = infer_flow(model, image1, image2, device, args.iterations)
        inference_s = time.perf_counter() - inference_start
        speed = np.hypot(flow[..., 0], flow[..., 1])

        stem = f"flow_{pair_index:04d}_{pair_index + 1:04d}"
        output_start = time.perf_counter()
        np.savez_compressed(flows_dir / f"{stem}.npz", flow_px_per_frame=flow)
        save_overlay(
            overlays_dir / f"{stem}.png",
            background,
            flow,
            pair_index,
            pair_index + 1,
            args.grid_step,
            args.quiver_scale,
        )
        output_s = time.perf_counter() - output_start
        row = {
            "pair": stem,
            "frame_1": first_path.name,
            "frame_2": second_path.name,
            "mean_speed_px_frame": float(speed.mean()),
            "median_speed_px_frame": float(np.median(speed)),
            "p99_speed_px_frame": float(np.percentile(speed, 99)),
            "max_speed_px_frame": float(speed.max()),
            "mean_speed_px_s": float(speed.mean() / args.delta_t_s),
            "loading_s": load_s,
            "inference_s": inference_s,
            "output_s": output_s,
        }
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)

    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    git_commit, git_dirty = git_provenance()
    metadata = {
        "input_dir": str(args.input_dir.resolve()),
        "pairs": args.pairs,
        "pattern": args.pattern,
        "image_shape": list(flow.shape),
        "input_scale": args.input_scale,
        "iterations": args.iterations,
        "grid_step": args.grid_step,
        "delta_t_s": args.delta_t_s,
        "model": str(args.model.resolve()),
        "device": str(device),
        "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "peak_gpu_memory_gib": (
            torch.cuda.max_memory_allocated(device) / 1024**3 if device.type == "cuda" else None
        ),
        "torch": torch.__version__,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "model_loading_s": model_loading_s,
        "pairs_total_s": time.perf_counter() - run_start,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Sequence results written to {output_dir}")


if __name__ == "__main__":
    main()
