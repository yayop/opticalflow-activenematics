"""Infer optical flow between two microscopy frames with the project RAFT model."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from RAFT.core.raft import RAFT  # noqa: E402
from RAFT.core.utils.utils import InputPadder  # noqa: E402


def git_provenance() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate a dense displacement field between two consecutive microscopy frames. "
            "The raw RAFT output is measured in pixels per frame."
        )
    )
    parser.add_argument("image1", type=Path, help="Frame at time t (TIFF/PNG/JPEG).")
    parser.add_argument("image2", type=Path, help="Frame at time t + dt.")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "weights.pth")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results" / "pair")
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--grid-step", type=int, default=20, help="Quiver spacing in pixels.")
    parser.add_argument("--quiver-scale", type=float, default=0.5)
    parser.add_argument(
        "--device", choices=("auto", "cuda", "cpu"), default="auto",
        help="Use CUDA on the cluster; CPU is intended only for diagnostics.",
    )
    parser.add_argument("--mixed-precision", action="store_true")
    parser.add_argument("--pixel-size-um", type=float, help="Micrometres per pixel.")
    parser.add_argument("--delta-t-s", type=float, help="Seconds between the two frames.")
    parser.add_argument(
        "--input-scale", choices=("upstream", "raft"), default="upstream",
        help=(
            "'upstream' reproduces the repository's 0..1 tensor input; 'raft' uses the "
            "standard RAFT 0..255 convention. Do not change this without validation."
        ),
    )
    return parser.parse_args()


def select_device(requested: str) -> torch.device:
    if requested == "auto":
        requested = "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false.")
    return torch.device(requested)


def load_image(path: Path, input_scale: str) -> tuple[torch.Tensor, np.ndarray]:
    with Image.open(path) as image:
        grayscale = np.asarray(image.convert("L"), dtype=np.float32)
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    if input_scale == "upstream":
        rgb /= 255.0
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)
    return tensor, grayscale


def load_model(model_path: Path, device: torch.device, mixed_precision: bool) -> RAFT:
    model_args = SimpleNamespace(
        small=False,
        mixed_precision=mixed_precision,
        alternate_corr=False,
        dropout=0,
    )
    model = RAFT(model_args)
    try:
        state = torch.load(model_path, map_location="cpu", weights_only=True)
    except TypeError:  # Compatibility with older PyTorch versions.
        state = torch.load(model_path, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    state = {key.removeprefix("module."): value for key, value in state.items()}
    model.load_state_dict(state)
    return model.to(device).eval()


def infer_flow(
    model: RAFT,
    image1: torch.Tensor,
    image2: torch.Tensor,
    device: torch.device,
    iterations: int,
) -> np.ndarray:
    if image1.shape != image2.shape:
        raise ValueError(f"The frames have different shapes: {image1.shape} vs {image2.shape}")
    padder = InputPadder(image1.shape, 8)
    image1, image2 = padder.pad(image1.to(device), image2.to(device))
    with torch.inference_mode():
        _, flow = model(image1, image2, iters=iterations, test_mode=True)
    return padder.unpad(flow).squeeze(0).permute(1, 2, 0).cpu().numpy()


def save_outputs(
    output_dir: Path,
    background: np.ndarray,
    flow_px_frame: np.ndarray,
    args: argparse.Namespace,
    device: torch.device,
    timings_s: dict[str, float],
    total_start: float,
) -> None:
    output_start = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    vx = flow_px_frame[..., 0]
    vy = flow_px_frame[..., 1]
    speed = np.hypot(vx, vy)

    arrays: dict[str, np.ndarray] = {
        "flow_px_per_frame": flow_px_frame,
        "vx_px_per_frame": vx,
        "vy_px_per_frame": vy,
        "speed_px_per_frame": speed,
    }
    units = "px/frame"
    vector_x, vector_y = vx, vy
    if (args.pixel_size_um is None) != (args.delta_t_s is None):
        raise ValueError("--pixel-size-um and --delta-t-s must be supplied together.")
    if args.pixel_size_um is not None:
        factor = args.pixel_size_um / args.delta_t_s
        arrays["velocity_um_per_s"] = flow_px_frame * factor
        arrays["speed_um_per_s"] = speed * factor
        vector_x, vector_y = arrays["velocity_um_per_s"][..., 0], arrays["velocity_um_per_s"][..., 1]
        units = "um/s"

    np.savez_compressed(output_dir / "flow.npz", **arrays)

    step = args.grid_step
    y, x = np.mgrid[0 : vx.shape[0] : step, 0 : vx.shape[1] : step]
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(background, cmap="gray")
    ax.quiver(
        x,
        y,
        vector_x[::step, ::step],
        vector_y[::step, ::step],
        color="red",
        scale_units="xy",
        angles="xy",
        scale=args.quiver_scale,
        width=0.001,
    )
    ax.set_title(f"Optical flow ({units})")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(output_dir / "velocity_overlay.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    timings_s["output_generation_s"] = time.perf_counter() - output_start
    timings_s["total_before_metadata_s"] = time.perf_counter() - total_start
    git_commit, git_dirty = git_provenance()
    metadata = {
        "image1": str(args.image1.resolve()),
        "image2": str(args.image2.resolve()),
        "model": str(args.model.resolve()),
        "shape": list(flow_px_frame.shape),
        "iterations": args.iterations,
        "input_scale": args.input_scale,
        "device": str(device),
        "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "torch": torch.__version__,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "timings_s": timings_s,
        "pixel_size_um": args.pixel_size_um,
        "delta_t_s": args.delta_t_s,
        "reported_units": units,
        "mean_speed_px_per_frame": float(speed.mean()),
        "median_speed_px_per_frame": float(np.median(speed)),
        "max_speed_px_per_frame": float(speed.max()),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    total_start = time.perf_counter()
    args = parse_args()
    if args.iterations < 1 or args.grid_step < 1:
        raise ValueError("--iterations and --grid-step must be positive integers.")
    if args.pixel_size_um is not None and (args.pixel_size_um <= 0 or args.delta_t_s <= 0):
        raise ValueError("Spatial and temporal calibration values must be positive.")

    timings_s: dict[str, float] = {}
    device = select_device(args.device)

    stage_start = time.perf_counter()
    image1, background = load_image(args.image1, args.input_scale)
    image2, _ = load_image(args.image2, args.input_scale)
    timings_s["image_loading_s"] = time.perf_counter() - stage_start

    stage_start = time.perf_counter()
    model = load_model(args.model, device, args.mixed_precision)
    timings_s["model_loading_s"] = time.perf_counter() - stage_start

    stage_start = time.perf_counter()
    flow = infer_flow(model, image1, image2, device, args.iterations)
    timings_s["inference_s"] = time.perf_counter() - stage_start
    save_outputs(args.output_dir, background, flow, args, device, timings_s, total_start)
    print(f"Results written to {args.output_dir.resolve()}")
    print("Timings (s): " + json.dumps(timings_s, sort_keys=True))


if __name__ == "__main__":
    main()
