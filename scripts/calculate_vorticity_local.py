"""Calculate image-coordinate vorticity for stored grid optical-flow fields.

The program is designed for the completed ``OpticalFlow_RAFT_grid12`` runs on
the ACTNEM NAS.  It is safe to restart: each NPZ is written atomically and a
batch completion marker is created only after every output has been validated.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np


DEFAULT_ROOTS = (
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\bulk2\OpticalFlow_RAFT_grid12"),
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\20241106_BULK001\OpticalFlow_RAFT_grid12"),
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\20241108_BULK\OpticalFlow_RAFT_grid12"),
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\20250227\OpticalFlow_RAFT_grid12"),
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\BULK\OpticalFlow_RAFT_grid12"),
    Path(r"\\ACTNEM\homes\Edgardo Rosas\Bulk Active Nematics Videos\Bulk_1_12_11\OpticalFlow_RAFT_grid12"),
)

FLOW_RE = re.compile(r"^flow_(\d+)_(\d+)\.npz$")
METHOD_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate vorticity from grid optical-flow fields in parallel."
    )
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        help="OpticalFlow_RAFT_grid12 result root. Repeat for multiple roots; defaults to all six NAS runs.",
    )
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 1, 48))
    parser.add_argument("--grid-step-px", type=float, default=12.0)
    parser.add_argument("--delta-t-s", type=float, default=0.5)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run a solid-body-rotation test and exit without touching result roots.",
    )
    return parser.parse_args()


def calculate_vorticity(flow: np.ndarray, grid_step_px: float) -> np.ndarray:
    """Return omega_image = d(v)/d(x) - d(u)/d(y), in inverse frames."""
    if flow.ndim != 3 or flow.shape[2] != 2 or min(flow.shape[:2]) < 3:
        raise ValueError(f"Expected an H x W x 2 field with H,W >= 3, got {flow.shape}")
    velocity = np.asarray(flow, dtype=np.float32)
    if not np.isfinite(velocity).all():
        raise ValueError("The source flow contains non-finite values.")
    u = velocity[..., 0]
    v = velocity[..., 1]
    dv_dx = np.gradient(v, grid_step_px, axis=1, edge_order=2)
    du_dy = np.gradient(u, grid_step_px, axis=0, edge_order=2)
    return np.asarray(dv_dx - du_dy, dtype=np.float32)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w+b", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, path)


def output_name(flow_path: Path) -> str:
    match = FLOW_RE.match(flow_path.name)
    if match is None:
        raise ValueError(f"Unexpected flow filename: {flow_path.name}")
    return f"vorticity_{int(match.group(1)):04d}_{int(match.group(2)):04d}.npz"


def field_stats(omega: np.ndarray) -> tuple[float, float, float, float, float]:
    return (
        float(np.mean(omega, dtype=np.float64)),
        float(np.mean(np.abs(omega), dtype=np.float64)),
        float(np.sqrt(np.mean(np.square(omega), dtype=np.float64))),
        float(np.min(omega)),
        float(np.max(omega)),
    )


def process_one(task: tuple[str, str, float]) -> dict[str, object]:
    """Worker entry point. All arguments are pickle-safe for Windows spawn."""
    source_text, output_text, grid_step_px = task
    source = Path(source_text)
    output = Path(output_text)
    reused = False
    if output.is_file():
        try:
            with np.load(output, allow_pickle=False) as existing:
                omega = existing["vorticity_image_per_frame"]
            if omega.ndim == 2 and omega.dtype == np.float32 and np.isfinite(omega).all():
                reused = True
        except (OSError, ValueError, KeyError):
            reused = False
    if not reused:
        with np.load(source, allow_pickle=False) as data:
            if "flow_px_per_frame" not in data:
                raise KeyError(f"Missing flow_px_per_frame in {source}")
            flow = data["flow_px_per_frame"]
        omega = calculate_vorticity(flow, grid_step_px)
        atomic_npz(output, vorticity_image_per_frame=omega)

    match = FLOW_RE.match(source.name)
    assert match is not None
    mean, mean_abs, rms, minimum, maximum = field_stats(omega)
    return {
        "pair_start": int(match.group(1)),
        "pair_end": int(match.group(2)),
        "shape": list(omega.shape),
        "mean_per_frame": mean,
        "mean_abs_per_frame": mean_abs,
        "rms_per_frame": rms,
        "min_per_frame": minimum,
        "max_per_frame": maximum,
        "bytes": output.stat().st_size,
        "reused": reused,
    }


def read_source_manifest(root: Path) -> dict[str, object]:
    for name in ("campaign_manifest.json", "manifest.json"):
        path = root / name
        if path.is_file():
            with path.open(encoding="utf-8-sig") as handle:
                return json.load(handle)
    return {}


def source_grid_step(manifest: dict[str, object]) -> float | None:
    value = manifest.get("storage_grid_step_px")
    return None if value is None else float(value)


def batch_is_complete(
    marker: Path, flow_count: int, expected_shape: list[int], grid_step_px: float, delta_t_s: float
) -> bool:
    if not marker.is_file():
        return False
    try:
        with marker.open(encoding="utf-8-sig") as handle:
            report = json.load(handle)
        return (
            report.get("verified") is True
            and report.get("method_version") == METHOD_VERSION
            and report.get("flow_count") == flow_count
            and report.get("shape") == expected_shape
            and math.isclose(float(report.get("grid_step_px")), grid_step_px)
            and math.isclose(float(report.get("delta_t_s")), delta_t_s)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def write_summary(path: Path, rows: Iterable[dict[str, object]], delta_t_s: float) -> None:
    columns = (
        "pair_start",
        "pair_end",
        "mean_per_frame",
        "mean_abs_per_frame",
        "rms_per_frame",
        "min_per_frame",
        "max_per_frame",
        "mean_per_s",
        "mean_abs_per_s",
        "rms_per_s",
        "min_per_s",
        "max_per_s",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            per_s = {
                key.replace("_per_frame", "_per_s"): float(value) / delta_t_s
                for key, value in row.items()
                if key.endswith("_per_frame")
            }
            writer.writerow({key: row.get(key, per_s.get(key)) for key in columns})
    os.replace(temporary, path)


def run_batch(
    executor: ProcessPoolExecutor,
    batch: Path,
    grid_step_px: float,
    delta_t_s: float,
    progress_every: int,
) -> dict[str, object]:
    flows = sorted((batch / "flows").glob("flow_*.npz"))
    if not flows:
        raise FileNotFoundError(f"No flow NPZ files in {batch / 'flows'}")
    first_name = output_name(flows[0])
    with np.load(flows[0], allow_pickle=False) as data:
        expected_shape = list(data["flow_px_per_frame"].shape[:2])
    marker = batch / "_VORTICITY_COMPLETE.json"
    if batch_is_complete(marker, len(flows), expected_shape, grid_step_px, delta_t_s):
        with marker.open(encoding="utf-8-sig") as handle:
            report = json.load(handle)
        print(f"SKIP {batch.name}: {len(flows)} verified fields", flush=True)
        return report

    output_dir = batch / "vorticity"
    tasks = [
        (str(flow), str(output_dir / output_name(flow)), grid_step_px)
        for flow in flows
    ]
    print(
        f"START {batch}: {len(tasks)} fields; first output {first_name}",
        flush=True,
    )
    started = time.perf_counter()
    rows: list[dict[str, object]] = []
    reused_count = 0
    for index, result in enumerate(executor.map(process_one, tasks, chunksize=4), start=1):
        rows.append(result)
        reused_count += int(bool(result["reused"]))
        if index % progress_every == 0 or index == len(tasks):
            elapsed = time.perf_counter() - started
            print(
                f"PROGRESS {batch.name}: {index}/{len(tasks)} "
                f"({index / elapsed:.1f} fields/s, {reused_count} reused)",
                flush=True,
            )

    rows.sort(key=lambda row: int(row["pair_start"]))
    shapes = {tuple(row["shape"]) for row in rows}
    if shapes != {tuple(expected_shape)}:
        raise ValueError(f"Inconsistent vorticity shapes in {batch}: {sorted(shapes)}")
    write_summary(batch / "vorticity_summary.csv", rows, delta_t_s)
    total_bytes = sum(int(row["bytes"]) for row in rows)
    elapsed = time.perf_counter() - started
    report: dict[str, object] = {
        "verified": True,
        "method_version": METHOD_VERSION,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "batch": batch.name,
        "pair_start": int(rows[0]["pair_start"]),
        "pair_end": int(rows[-1]["pair_start"]),
        "flow_count": len(rows),
        "shape": expected_shape,
        "dtype": "float32",
        "grid_step_px": grid_step_px,
        "delta_t_s": delta_t_s,
        "vorticity_key": "vorticity_image_per_frame",
        "formula": "omega_image = d(v)/d(x) - d(u)/d(y)",
        "coordinate_convention": "+x right, +y down; Cartesian vorticity has the opposite sign",
        "derivative": "numpy.gradient, second-order central interior and second-order one-sided boundaries",
        "total_bytes": total_bytes,
        "elapsed_s": elapsed,
        "fields_per_s": len(rows) / elapsed,
        "reused_count": reused_count,
        "failures": [],
    }
    atomic_json(batch / "vorticity_metadata.json", report)
    atomic_json(marker, report)
    print(f"COMPLETE {batch.name}: {len(rows)} fields in {elapsed:.1f} s", flush=True)
    return report


def run_root(
    executor: ProcessPoolExecutor,
    root: Path,
    grid_step_px: float,
    delta_t_s: float,
    progress_every: int,
) -> dict[str, object]:
    if not root.is_dir():
        raise FileNotFoundError(f"Result root does not exist: {root}")
    manifest = read_source_manifest(root)
    stored_step = source_grid_step(manifest)
    if stored_step is not None and not math.isclose(stored_step, grid_step_px):
        raise ValueError(
            f"{root} stores a {stored_step:g} px grid, not the requested {grid_step_px:g} px grid."
        )
    batches = sorted(path for path in root.glob("batch_*_*") if (path / "flows").is_dir())
    if not batches:
        raise FileNotFoundError(f"No batch directories found in {root}")
    print(f"DATASET {root}: {len(batches)} batches", flush=True)
    reports = [
        run_batch(executor, batch, grid_step_px, delta_t_s, progress_every)
        for batch in batches
    ]
    total_count = sum(int(report["flow_count"]) for report in reports)
    result: dict[str, object] = {
        "complete": True,
        "method_version": METHOD_VERSION,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "result_root": str(root),
        "source_manifest": "campaign_manifest.json"
        if (root / "campaign_manifest.json").is_file()
        else "manifest.json",
        "stored_pair_count": total_count,
        "grid_step_px": grid_step_px,
        "delta_t_s": delta_t_s,
        "vorticity_key": "vorticity_image_per_frame",
        "formula": "omega_image = d(v)/d(x) - d(u)/d(y)",
        "coordinate_convention": "+x right, +y down; Cartesian vorticity has the opposite sign",
        "batches": reports,
    }
    atomic_json(root / "vorticity_manifest.json", result)
    print(f"DATASET COMPLETE {root}: {total_count} fields", flush=True)
    return result


def self_test(grid_step_px: float) -> None:
    height, width = 31, 37
    angular_rate = np.float32(0.075)
    y, x = np.mgrid[:height, :width].astype(np.float32) * np.float32(grid_step_px)
    flow = np.stack((-angular_rate * y, angular_rate * x), axis=-1)
    omega = calculate_vorticity(flow, grid_step_px)
    expected = float(2 * angular_rate)
    maximum_error = float(np.max(np.abs(omega - expected)))
    if maximum_error > 2e-6:
        raise AssertionError(
            f"Solid-body rotation test failed: expected {expected}, max error {maximum_error}"
        )
    print(
        json.dumps(
            {
                "self_test": "passed",
                "field": "solid-body rotation",
                "angular_rate_per_frame": float(angular_rate),
                "expected_vorticity_per_frame": expected,
                "maximum_absolute_error": maximum_error,
            },
            indent=2,
        )
    )


def main() -> None:
    args = parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    if args.grid_step_px <= 0 or args.delta_t_s <= 0:
        raise ValueError("--grid-step-px and --delta-t-s must be positive")
    if args.progress_every < 1:
        raise ValueError("--progress-every must be positive")
    if args.self_test:
        self_test(args.grid_step_px)
        return

    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    print(
        json.dumps(
            {
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "workers": args.workers,
                "logical_processors": os.cpu_count(),
                "grid_step_px": args.grid_step_px,
                "delta_t_s": args.delta_t_s,
                "roots": [str(root) for root in roots],
            },
            indent=2,
        ),
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        reports = [
            run_root(
                executor,
                root,
                args.grid_step_px,
                args.delta_t_s,
                args.progress_every,
            )
            for root in roots
        ]
    print(
        json.dumps(
            {
                "complete": True,
                "dataset_count": len(reports),
                "total_fields": sum(int(report["stored_pair_count"]) for report in reports),
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
