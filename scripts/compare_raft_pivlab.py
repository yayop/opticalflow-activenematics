"""Render matched RAFT/PIVlab/PIVlab2 panels and compute agreement metrics."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.io
from PIL import Image
from scipy.ndimage import map_coordinates


def parse_pair_spec(value: str) -> list[int]:
    indices: list[int] = []
    for part in value.split(","):
        bounds = [int(item) for item in part.strip().split("-")]
        if len(bounds) == 1:
            indices.append(bounds[0])
        elif len(bounds) == 2 and bounds[1] >= bounds[0]:
            indices.extend(range(bounds[0], bounds[1] + 1))
        else:
            raise argparse.ArgumentTypeError(f"Invalid pair specification: {part}")
    return sorted(set(indices))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pivlab", type=Path, required=True)
    parser.add_argument("--pivlab2", type=Path, required=True)
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--frame-pattern", default="Frame_{index:04d}.tif")
    parser.add_argument("--raft-flows-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=parse_pair_spec, default=parse_pair_spec("1-12"))
    parser.add_argument("--subsample", type=int, default=3)
    parser.add_argument("--quiver-scale", type=float, default=0.25)
    return parser.parse_args()


def load_piv(path: Path) -> dict[str, np.ndarray]:
    return scipy.io.loadmat(path, variable_names=["x", "y", "u_smoothed", "v_smoothed"])


def piv_field(data: dict[str, np.ndarray], pair_index: int) -> tuple[np.ndarray, ...]:
    offset = pair_index - 1
    return (
        data["x"][offset, 0].astype(np.float32) - 1,
        data["y"][offset, 0].astype(np.float32) - 1,
        data["u_smoothed"][0, offset].astype(np.float32),
        data["v_smoothed"][0, offset].astype(np.float32),
    )


def normalized_background(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        raw = np.asarray(image)
    if not np.issubdtype(raw.dtype, np.integer):
        raise ValueError(f"Expected an integer grayscale TIFF: {path}")
    return raw.astype(np.float32) / float(np.iinfo(raw.dtype).max)


def agreement(raft_u: np.ndarray, raft_v: np.ndarray, piv_u: np.ndarray, piv_v: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(raft_u) & np.isfinite(raft_v) & np.isfinite(piv_u) & np.isfinite(piv_v)
    ru, rv, pu, pv = raft_u[valid], raft_v[valid], piv_u[valid], piv_v[valid]
    cosine = (ru * pu + rv * pv) / (np.hypot(ru, rv) * np.hypot(pu, pv) + 1e-8)
    return {
        "corr_u": float(np.corrcoef(ru, pu)[0, 1]),
        "corr_v": float(np.corrcoef(rv, pv)[0, 1]),
        "mean_direction_cosine": float(cosine.mean()),
        "raft_mean_speed_px_frame": float(np.hypot(ru, rv).mean()),
        "piv_mean_speed_px_frame": float(np.hypot(pu, pv).mean()),
    }


def draw_panel(
    ax: plt.Axes,
    background: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    title: str,
    color: str,
    subsample: int,
    quiver_scale: float,
) -> None:
    ax.imshow(background, cmap="gray", vmin=0, vmax=1)
    selection = (slice(None, None, subsample), slice(None, None, subsample))
    ax.quiver(
        x[selection],
        y[selection],
        u[selection],
        v[selection],
        color=color,
        scale_units="xy",
        angles="xy",
        scale=quiver_scale,
        width=0.002,
    )
    ax.set_title(title)
    ax.set_axis_off()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    piv1 = load_piv(args.pivlab)
    piv2 = load_piv(args.pivlab2)
    rows: list[dict[str, float | int | str]] = []

    for pair_index in args.pairs:
        background = normalized_background(
            args.frames_dir / args.frame_pattern.format(index=pair_index)
        )
        flow = np.load(args.raft_flows_dir / f"flow_{pair_index:04d}_{pair_index + 1:04d}.npz")[
            "flow_px_per_frame"
        ]
        x1, y1, u1, v1 = piv_field(piv1, pair_index)
        x2, y2, u2, v2 = piv_field(piv2, pair_index)
        raft_u1 = map_coordinates(flow[..., 0], [y1, x1], order=1)
        raft_v1 = map_coordinates(flow[..., 1], [y1, x1], order=1)
        raft_u2 = map_coordinates(flow[..., 0], [y2, x2], order=1)
        raft_v2 = map_coordinates(flow[..., 1], [y2, x2], order=1)
        metrics1 = agreement(raft_u1, raft_v1, u1, v1)
        metrics2 = agreement(raft_u2, raft_v2, u2, v2)

        figure, axes = plt.subplots(1, 3, figsize=(15, 8), sharex=True, sharey=True)
        draw_panel(
            axes[0], background, x1, y1, raft_u1, raft_v1,
            f"RAFT\nmean={metrics1['raft_mean_speed_px_frame']:.2f} px/frame",
            "#ff2d2d", args.subsample, args.quiver_scale,
        )
        draw_panel(
            axes[1], background, x1, y1, u1, v1,
            f"PIVlab\nmean={metrics1['piv_mean_speed_px_frame']:.2f}; cos={metrics1['mean_direction_cosine']:.3f}",
            "#0066cc", args.subsample, args.quiver_scale,
        )
        draw_panel(
            axes[2], background, x2, y2, u2, v2,
            f"PIVlab2\nmean={metrics2['piv_mean_speed_px_frame']:.2f}; cos={metrics2['mean_direction_cosine']:.3f}",
            "#00a36c", args.subsample, args.quiver_scale,
        )
        figure.suptitle(f"Frame {pair_index:04d} → {pair_index + 1:04d}; common vector scale")
        figure.tight_layout()
        figure.savefig(
            args.output_dir / f"comparison_{pair_index:04d}_{pair_index + 1:04d}.png",
            dpi=160,
            bbox_inches="tight",
        )
        plt.close(figure)

        row: dict[str, float | int | str] = {"pair_index": pair_index}
        row.update({f"pivlab_{key}": value for key, value in metrics1.items()})
        row.update({f"pivlab2_{key}": value for key, value in metrics2.items()})
        rows.append(row)

    with (args.output_dir / "agreement_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
