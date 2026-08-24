"""Compare RAFT fields obtained before and after image preprocessing."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


def parse_pair_spec(value: str) -> list[int]:
    indices: set[int] = set()
    for part in value.split(","):
        bounds = [int(item) for item in part.strip().split("-")]
        if len(bounds) == 1:
            indices.add(bounds[0])
        elif len(bounds) == 2 and bounds[1] >= bounds[0]:
            indices.update(range(bounds[0], bounds[1] + 1))
        else:
            raise argparse.ArgumentTypeError(f"Invalid pair specification: {part}")
    return sorted(indices)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare matched RAFT fields from raw and preprocessed frames."
    )
    parser.add_argument("--raw-frames-dir", type=Path, required=True)
    parser.add_argument("--processed-frames-dir", type=Path, required=True)
    parser.add_argument("--raw-flows-dir", type=Path, required=True)
    parser.add_argument("--processed-flows-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--raw-pattern", default="Frame_{index:04d}.tif")
    parser.add_argument("--processed-pattern", default="frame_{index:04d}.tif")
    parser.add_argument("--pairs", type=parse_pair_spec, default=parse_pair_spec("1-12"))
    parser.add_argument("--grid-step", type=int, default=24)
    parser.add_argument("--quiver-scale", type=float, default=0.5)
    parser.add_argument("--interior-margin", type=int, default=64)
    return parser.parse_args()


def normalized_background(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        raw = np.asarray(image)
    if not np.issubdtype(raw.dtype, np.integer):
        raise ValueError(f"Expected an integer grayscale TIFF: {path}")
    return raw.astype(np.float32) / float(np.iinfo(raw.dtype).max)


def load_flow(directory: Path, pair_index: int) -> np.ndarray:
    path = directory / f"flow_{pair_index:04d}_{pair_index + 1:04d}.npz"
    with np.load(path) as data:
        return data["flow_px_per_frame"]


def metrics(
    raw: np.ndarray, processed: np.ndarray, interior_margin: int
) -> dict[str, float]:
    difference = processed - raw
    endpoint_error = np.linalg.norm(difference, axis=-1)
    raw_speed = np.linalg.norm(raw, axis=-1)
    processed_speed = np.linalg.norm(processed, axis=-1)
    cosine = np.sum(raw * processed, axis=-1) / (
        raw_speed * processed_speed + 1e-8
    )
    if interior_margin:
        interior = (
            slice(interior_margin, -interior_margin),
            slice(interior_margin, -interior_margin),
        )
        interior_error = endpoint_error[interior]
        interior_cosine = cosine[interior]
    else:
        interior_error = endpoint_error
        interior_cosine = cosine
    return {
        "raw_mean_speed_px_frame": float(raw_speed.mean()),
        "processed_mean_speed_px_frame": float(processed_speed.mean()),
        "mean_speed_change_px_frame": float(processed_speed.mean() - raw_speed.mean()),
        "mean_endpoint_error_px_frame": float(endpoint_error.mean()),
        "p95_endpoint_error_px_frame": float(np.percentile(endpoint_error, 95)),
        "component_rmse_px_frame": float(np.sqrt(np.mean(difference**2))),
        "mean_direction_cosine": float(cosine.mean()),
        "interior_mean_endpoint_error_px_frame": float(interior_error.mean()),
        "interior_p95_endpoint_error_px_frame": float(
            np.percentile(interior_error, 95)
        ),
        "interior_mean_direction_cosine": float(interior_cosine.mean()),
        "corr_u": float(np.corrcoef(raw[..., 0].ravel(), processed[..., 0].ravel())[0, 1]),
        "corr_v": float(np.corrcoef(raw[..., 1].ravel(), processed[..., 1].ravel())[0, 1]),
    }


def draw_flow(
    ax: plt.Axes,
    background: np.ndarray,
    flow: np.ndarray,
    title: str,
    color: str,
    grid_step: int,
    quiver_scale: float,
) -> None:
    y, x = np.mgrid[0 : flow.shape[0] : grid_step, 0 : flow.shape[1] : grid_step]
    ax.imshow(background, cmap="gray", vmin=0, vmax=1)
    ax.quiver(
        x,
        y,
        flow[::grid_step, ::grid_step, 0],
        flow[::grid_step, ::grid_step, 1],
        color=color,
        scale_units="xy",
        angles="xy",
        scale=quiver_scale,
        width=0.0013,
    )
    ax.set_title(title)
    ax.set_axis_off()


def main() -> None:
    args = parse_args()
    if args.interior_margin < 0:
        raise ValueError("Interior margin must be non-negative.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, float | int]] = []

    for pair_index in args.pairs:
        raw_background = normalized_background(
            args.raw_frames_dir / args.raw_pattern.format(index=pair_index)
        )
        processed_background = normalized_background(
            args.processed_frames_dir
            / args.processed_pattern.format(index=pair_index)
        )
        raw_flow = load_flow(args.raw_flows_dir, pair_index)
        processed_flow = load_flow(args.processed_flows_dir, pair_index)
        if 2 * args.interior_margin >= min(raw_flow.shape[:2]):
            raise ValueError("Interior margin is too large for the flow dimensions.")
        pair_metrics = metrics(raw_flow, processed_flow, args.interior_margin)
        endpoint_error = np.linalg.norm(processed_flow - raw_flow, axis=-1)

        figure, axes = plt.subplots(1, 3, figsize=(15, 8), sharex=True, sharey=True)
        draw_flow(
            axes[0],
            raw_background,
            raw_flow,
            f"Raw\nmean={pair_metrics['raw_mean_speed_px_frame']:.2f} px/frame",
            "#ff2d2d",
            args.grid_step,
            args.quiver_scale,
        )
        draw_flow(
            axes[1],
            processed_background,
            processed_flow,
            "Background median divided\n"
            f"mean={pair_metrics['processed_mean_speed_px_frame']:.2f} px/frame",
            "#00a36c",
            args.grid_step,
            args.quiver_scale,
        )
        image = axes[2].imshow(endpoint_error, cmap="magma", vmin=0)
        axes[2].set_title(
            "Vector difference magnitude\n"
            f"mean={pair_metrics['mean_endpoint_error_px_frame']:.3f}; "
            f"cos={pair_metrics['mean_direction_cosine']:.3f}"
        )
        axes[2].set_axis_off()
        figure.colorbar(image, ax=axes[2], fraction=0.046, label="px/frame")
        figure.suptitle(
            f"Frame {pair_index:04d} → {pair_index + 1:04d}; common vector scale"
        )
        figure.tight_layout()
        figure.savefig(
            args.output_dir
            / f"preprocessing_comparison_{pair_index:04d}_{pair_index + 1:04d}.png",
            dpi=160,
            bbox_inches="tight",
        )
        plt.close(figure)

        rows.append({"pair_index": pair_index, **pair_metrics})

    with (args.output_dir / "preprocessing_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
