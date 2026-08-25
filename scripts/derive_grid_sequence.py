"""Derive a regularly sampled flow sequence without rerunning inference."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_run", type=Path)
    parser.add_argument("output_run", type=Path)
    parser.add_argument("--grid-step", type=int, default=12)
    parser.add_argument("--dtype", choices=("float16", "float32"), default="float16")
    return parser.parse_args()


def verify_existing_batch(
    output_batch: Path, pair_start: int, pair_end: int, expected_shape: tuple[int, ...], dtype: str
) -> bool:
    report_path = output_batch / "verification.json"
    if not report_path.is_file():
        return False
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return (
        report.get("verified") is True
        and report.get("pair_start") == pair_start
        and report.get("pair_end") == pair_end
        and report.get("shape") == list(expected_shape)
        and report.get("dtype") == dtype
    )


def main() -> None:
    args = parse_args()
    if args.grid_step < 1:
        raise ValueError("Grid step must be positive.")
    source_manifest = json.loads(
        (args.input_run / "manifest.json").read_text(encoding="utf-8-sig")
    )
    source_batches_value = source_manifest["batches"]
    if isinstance(source_batches_value, dict):
        source_batches_value = [source_batches_value]
    source_batches = sorted(source_batches_value, key=lambda item: item["pair_start"])
    full_height, full_width = source_batches[0]["shape"][:2]
    expected_shape = (
        math.ceil(full_height / args.grid_step),
        math.ceil(full_width / args.grid_step),
        2,
    )
    args.output_run.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []

    for source_report in source_batches:
        pair_start = source_report["pair_start"]
        pair_end = source_report["pair_end"]
        batch_name = f"batch_{pair_start:04d}_{pair_end:04d}"
        input_batch = args.input_run / batch_name
        output_batch = args.output_run / batch_name
        if verify_existing_batch(output_batch, pair_start, pair_end, expected_shape, args.dtype):
            reports.append(json.loads((output_batch / "verification.json").read_text(encoding="utf-8")))
            continue

        output_flows = output_batch / "flows"
        output_flows.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        for pair_index in range(pair_start, pair_end + 1):
            name = f"flow_{pair_index:04d}_{pair_index + 1:04d}.npz"
            with np.load(input_batch / "flows" / name) as data:
                dense = data["flow_px_per_frame"]
                sampled = dense[:: args.grid_step, :: args.grid_step].astype(args.dtype, copy=False)
            if sampled.shape != expected_shape or not np.isfinite(sampled).all():
                raise ValueError(f"Invalid sampled field: {name}")
            output_path = output_flows / name
            np.savez_compressed(output_path, flow_px_per_frame=sampled)
            total_bytes += output_path.stat().st_size

        shutil.copy2(input_batch / "summary.csv", output_batch / "summary.csv")
        metadata = json.loads((input_batch / "metadata.json").read_text(encoding="utf-8"))
        metadata.update(
            {
                "source_dense_run": str(args.input_run.resolve()),
                "stored_flow_shape": list(expected_shape),
                "storage_grid_step_px": args.grid_step,
                "storage_grid_origin_xy_px": [0, 0],
                "flow_dtype": args.dtype,
                "derived_without_inference": True,
            }
        )
        (output_batch / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        report: dict[str, object] = {
            "verified": True,
            "pair_start": pair_start,
            "pair_end": pair_end,
            "flow_count": pair_end - pair_start + 1,
            "flow_bytes": total_bytes,
            "dtype": args.dtype,
            "shape": list(expected_shape),
            "full_image_shape": [full_height, full_width],
            "storage_grid_step_px": args.grid_step,
            "failures": [],
        }
        (output_batch / "verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        reports.append(report)

    all_rows: list[dict[str, str]] = []
    for report in reports:
        batch_name = f"batch_{report['pair_start']:04d}_{report['pair_end']:04d}"
        with (args.output_run / batch_name / "summary.csv").open(newline="", encoding="utf-8") as handle:
            all_rows.extend(csv.DictReader(handle))
    with (args.output_run / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(sorted(all_rows, key=lambda row: row["pair"]))

    manifest = {
        "run_name": args.output_run.name,
        "source_dense_run": str(args.input_run.resolve()),
        "frame_count": source_manifest["frame_count"],
        "stored_pair_count": sum(int(report["flow_count"]) for report in reports),
        "complete_sequence": source_manifest.get(
            "complete_sequence",
            sum(int(report["flow_count"]) for report in reports)
            == int(source_manifest["frame_count"]) - 1,
        ),
        "flow_dtype": args.dtype,
        "storage_grid_step_px": args.grid_step,
        "storage_grid_origin_xy_px": [0, 0],
        "derived_without_inference": True,
        "batches": reports,
    }
    (args.output_run / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output_run), "pairs": manifest["stored_pair_count"]}, indent=2))


if __name__ == "__main__":
    main()
