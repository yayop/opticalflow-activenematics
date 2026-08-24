"""Verify a downloaded sequence batch before remote cleanup."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--pair-start", type=int, required=True)
    parser.add_argument("--pair-end", type=int, required=True)
    parser.add_argument("--dtype", choices=("float16", "float32"), required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    expected_pairs = list(range(args.pair_start, args.pair_end + 1))
    flows_dir = args.output_dir / "flows"
    failures: list[str] = []
    total_bytes = 0
    flow_count = 0

    for pair_index in expected_pairs:
        path = flows_dir / f"flow_{pair_index:04d}_{pair_index + 1:04d}.npz"
        if not path.is_file():
            failures.append(f"missing: {path.name}")
            continue
        flow_count += 1
        try:
            with np.load(path) as data:
                flow = data["flow_px_per_frame"]
                if flow.shape != (args.height, args.width, 2):
                    failures.append(f"shape {flow.shape}: {path.name}")
                if flow.dtype != np.dtype(args.dtype):
                    failures.append(f"dtype {flow.dtype}: {path.name}")
                if not np.isfinite(flow).all():
                    failures.append(f"non-finite values: {path.name}")
        except Exception as error:  # noqa: BLE001 - report corrupt artifacts
            failures.append(f"unreadable {path.name}: {error}")
        total_bytes += path.stat().st_size

    summary_path = args.output_dir / "summary.csv"
    metadata_path = args.output_dir / "metadata.json"
    if not summary_path.is_file():
        failures.append("missing: summary.csv")
    else:
        with summary_path.open(newline="", encoding="utf-8") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        if row_count != len(expected_pairs):
            failures.append(f"summary rows {row_count}, expected {len(expected_pairs)}")
    if not metadata_path.is_file():
        failures.append("missing: metadata.json")
    else:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("pairs") != expected_pairs:
            failures.append("metadata pair range does not match")
        if metadata.get("flow_dtype") != args.dtype:
            failures.append("metadata dtype does not match")

    report = {
        "verified": not failures,
        "pair_start": args.pair_start,
        "pair_end": args.pair_end,
        "flow_count": flow_count,
        "flow_bytes": total_bytes,
        "dtype": args.dtype,
        "shape": [args.height, args.width, 2],
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)
    (args.output_dir / "verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
