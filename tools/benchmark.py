from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def _percentile(values: list[float], percentile: float):
    if not values:
        return None
    ordered = sorted(values)
    index = int(math.ceil(percentile * len(ordered))) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


def summarize(path: Path, max_gap_s: float) -> dict:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    observations = [row for row in records if row.get("type") == "observation"]
    if not observations:
        raise ValueError("run contains no observation rows")
    observations.sort(key=lambda row: float(row.get("run_time_s", row["source_time_s"])))
    ages = [float(row["capture_age_ms"]) for row in observations if row.get("capture_age_ms") is not None]
    processing = [float(row["processing_ms"]) for row in observations if row.get("processing_ms") is not None]
    supported_seconds = 0.0
    elapsed_seconds = 0.0
    valid_observations = 0
    unknown_observations = 0
    for row in observations:
        if row.get("eye_state") == "unknown":
            unknown_observations += 1
        else:
            valid_observations += 1
    for current, following in zip(observations, observations[1:]):
        t0 = float(current.get("run_time_s", current["source_time_s"]))
        t1 = float(following.get("run_time_s", following["source_time_s"]))
        dt = t1 - t0
        if dt <= 0:
            continue
        elapsed_seconds += dt
        if dt <= max_gap_s and current.get("eye_state") != "unknown" and current.get("capture_health") == "available":
            supported_seconds += dt
    resources = [row for row in records if row.get("type") == "resource_sample"]
    cpu = [float(row["process_cpu_percent"]) for row in resources if row.get("process_cpu_percent") is not None]
    rss = [float(row["rss_mb"]) for row in resources if row.get("rss_mb") is not None]
    return {
        "schema_version": 1,
        "observations": len(observations),
        "valid_eye_observations": valid_observations,
        "unknown_eye_observations": unknown_observations,
        "valid_eye_row_fraction": valid_observations / len(observations),
        "time_weighted_eye_capture_coverage": supported_seconds / elapsed_seconds if elapsed_seconds > 0 else None,
        "time_weighted_interval_s": elapsed_seconds,
        "capture_age_p95_ms": _percentile(ages, 0.95),
        "processing_p95_ms": _percentile(processing, 0.95),
        "process_cpu_percent_p95": _percentile(cpu, 0.95),
        "rss_mb_p95": _percentile(rss, 0.95),
        "resource_samples": len(resources),
        "alert_transitions": sum(row.get("type") == "alert_transition" for row in records),
        "log_records": len(records),
        "claim_limit": "Coverage and timing use application timestamps; camera sensor-to-audio latency needs physical measurement.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize one application run")
    parser.add_argument("run_dir")
    parser.add_argument("--max-gap-s", type=float, default=0.25)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        run_dir = Path(args.run_dir)
        result = summarize(run_dir / "observations.jsonl", args.max_gap_s)
        output = Path(args.output) if args.output else run_dir / "benchmark.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(json.dumps(result, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"benchmark: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
