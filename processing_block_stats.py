#!/usr/bin/env python3
"""
Group processing.log BLOCK-BATCH metrics by windows and forecast future performance.

Expected log line format:
    [timestamp] <test_name> BLOCK-BATCH <from_block> <to_block> <creates> <updates> <deletes> <extends> <ownerchanges> <processingTime>

Example:
    [2026-03-13T15:31:48+01:00] perf_test_20260313_1531 BLOCK-BATCH 1 1 175 0 0 0 0 175 19

Examples:
    python processing_block_stats.py
    python processing_block_stats.py --test-name perf_test_20260313_1534
    python processing_block_stats.py --window 10 --forecast-step 100 --forecast-horizon 10000
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


BLOCK_BATCH_LINE_RE = re.compile(
    r"\]\s+(?P<test>\S+)\s+BLOCK-BATCH\s+"
    r"(?P<from_block>\d+)\s+"
    r"(?P<to_block>\d+)\s+"
    r"(?P<creates>\d+)\s+"
    r"(?P<updates>\d+)\s+"
    r"(?P<deletes>\d+)\s+"
    r"(?P<extends>\d+)\s+"
    r"(?P<owner_changes>\d+)\s+"
    r"\d+\s+"                           # total_events (sum field, not used)
    r"(?P<processing_time>\d+)\s*$"
)


@dataclass
class BatchRecord:
    test_name: str
    from_block: int
    to_block: int
    creates: int
    updates: int
    deletes: int
    extends: int
    owner_changes: int
    processing_time_ms: float


@dataclass
class WindowStats:
    block_start: int
    block_end: int
    count: int
    mean: float
    median: float
    p90: float
    p99: float
    min_v: float
    max_v: float
    mean_creates: float
    mean_updates: float
    mean_deletes: float
    mean_extends: float
    mean_owner_changes: float
    is_prediction: bool = False


def percentile(values: List[float], q: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil(q * len(sorted_vals)) - 1)))
    return float(sorted_vals[idx])


def parse_processing_log(log_path: Path) -> List[BatchRecord]:
    records: List[BatchRecord] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            match = BLOCK_BATCH_LINE_RE.search(line)
            if not match:
                continue
            records.append(
                BatchRecord(
                    test_name=match.group("test"),
                    from_block=int(match.group("from_block")),
                    to_block=int(match.group("to_block")),
                    creates=int(match.group("creates")),
                    updates=int(match.group("updates")),
                    deletes=int(match.group("deletes")),
                    extends=int(match.group("extends")),
                    owner_changes=int(match.group("owner_changes")),
                    processing_time_ms=float(match.group("processing_time")),
                )
            )
    return records


def choose_test_name(records: Iterable[BatchRecord], explicit_test_name: Optional[str]) -> str:
    if explicit_test_name:
        return explicit_test_name

    # Default to last test name found in log ordering.
    last_name = ""
    for r in records:
        last_name = r.test_name
    if not last_name:
        raise ValueError("No BLOCK-BATCH lines found in log file")
    return last_name


def build_windows(records: List[BatchRecord], window_size: int) -> List[WindowStats]:
    if not records:
        return []

    records = sorted(records, key=lambda r: r.from_block)
    windows: List[WindowStats] = []

    i = 0
    while i < len(records):
        chunk = records[i : i + window_size]
        times = [r.processing_time_ms for r in chunk]

        windows.append(
            WindowStats(
                block_start=chunk[0].from_block,
                block_end=chunk[-1].to_block,
                count=len(chunk),
                mean=statistics.mean(times),
                median=statistics.median(times),
                p90=percentile(times, 0.90),
                p99=percentile(times, 0.99),
                min_v=min(times),
                max_v=max(times),
                mean_creates=statistics.mean(r.creates for r in chunk),
                mean_updates=statistics.mean(r.updates for r in chunk),
                mean_deletes=statistics.mean(r.deletes for r in chunk),
                mean_extends=statistics.mean(r.extends for r in chunk),
                mean_owner_changes=statistics.mean(r.owner_changes for r in chunk),
                is_prediction=False,
            )
        )
        i += window_size

    return windows


def linear_fit(xs: List[float], ys: List[float]) -> tuple[float, float]:
    """Return slope and intercept for y = slope*x + intercept."""
    if len(xs) < 2 or len(ys) < 2:
        return 0.0, ys[-1] if ys else 0.0

    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)

    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0, y_mean

    slope = num / den
    intercept = y_mean - slope * x_mean
    return slope, intercept


def build_predictions(
    base_windows: List[WindowStats],
    forecast_step: int,
    forecast_horizon: int,
) -> List[WindowStats]:
    if not base_windows:
        return []

    xs = [float(w.block_end) for w in base_windows]
    ys = [w.mean for w in base_windows]
    slope, intercept = linear_fit(xs, ys)

    last_end = base_windows[-1].block_end
    preds: List[WindowStats] = []

    # Keep operation metrics flat (last known means); predict time via linear trend.
    ref = base_windows[-1]

    for step in range(forecast_step, forecast_horizon + 1, forecast_step):
        block_end = last_end + step
        block_start = block_end - forecast_step + 1
        pred_mean = max(0.0, slope * block_end + intercept)

        preds.append(
            WindowStats(
                block_start=block_start,
                block_end=block_end,
                count=forecast_step,
                mean=pred_mean,
                median=pred_mean,
                p90=pred_mean,
                p99=pred_mean,
                min_v=pred_mean,
                max_v=pred_mean,
                mean_creates=ref.mean_creates,
                mean_updates=ref.mean_updates,
                mean_deletes=ref.mean_deletes,
                mean_extends=ref.mean_extends,
                mean_owner_changes=ref.mean_owner_changes,
                is_prediction=True,
            )
        )

    return preds


def print_table(title: str, rows: List[WindowStats]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(
        "range        type    n   mean_ms  median   p90    p99    min    max  "
        "creates  updates  deletes  extends  ownerchg"
    )

    for row in rows:
        range_label = f"{row.block_start:>6}-{row.block_end:<6}"
        kind = "pred" if row.is_prediction else "actual"
        print(
            f"{range_label}  {kind:<6} {row.count:>4} "
            f"{row.mean:>8.2f} {row.median:>7.2f} {row.p90:>6.2f} {row.p99:>6.2f} "
            f"{row.min_v:>6.2f} {row.max_v:>6.2f} "
            f"{row.mean_creates:>8.2f} {row.mean_updates:>8.2f} {row.mean_deletes:>8.2f} "
            f"{row.mean_extends:>8.2f} {row.mean_owner_changes:>8.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Grouped stats + growth forecast from processing.log")
    parser.add_argument(
        "--log-file",
        default="op-geth-simulator/processing.log",
        help="Path to processing.log",
    )
    parser.add_argument(
        "--test-name",
        default=None,
        help="Filter by test name. If omitted, script picks the last test in the log.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=10,
        help="Actual aggregation window in blocks (default: 10)",
    )
    parser.add_argument(
        "--forecast-step",
        type=int,
        default=100,
        help="Prediction row step size in blocks (default: 100)",
    )
    parser.add_argument(
        "--forecast-horizon",
        type=int,
        default=10000,
        help="How far to forecast beyond latest block (default: 10000)",
    )
    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    all_records = parse_processing_log(log_path)
    if not all_records:
        raise ValueError("No BLOCK-BATCH records found in log file")

    chosen_test = choose_test_name(all_records, args.test_name)
    records = [r for r in all_records if r.test_name == chosen_test]
    if not records:
        raise ValueError(f"No records found for test: {chosen_test}")

    windowed = build_windows(records, args.window)
    predicted = build_predictions(windowed, args.forecast_step, args.forecast_horizon)

    print(f"Test name: {chosen_test}")
    print(f"Source file: {log_path}")
    print(f"Batches analyzed: {len(records)} (blocks {min(r.from_block for r in records)} .. {max(r.to_block for r in records)})")

    print_table(f"Actual grouped stats (every {args.window} blocks)", windowed)
    print_table(
        f"Forecast rows (every {args.forecast_step} blocks up to +{args.forecast_horizon})",
        predicted,
    )


if __name__ == "__main__":
    main()
