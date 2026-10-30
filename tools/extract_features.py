from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _read_intervals(path: Path, subject_id: str, channel: str):
    intervals = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("subject_id") != subject_id or row.get("channel") != channel:
                continue
            start, end, label = float(row["start_s"]), float(row["end_s"]), int(row["label"])
            if end < start or label not in (0, 1):
                raise ValueError("annotation intervals need end >= start and binary label")
            intervals.append((start, end, label))
    return intervals


def main() -> int:
    parser = argparse.ArgumentParser(description="Join local feature logs to explicit event/negative annotations")
    parser.add_argument("observations_jsonl")
    parser.add_argument("--annotations", required=True, help="CSV: subject_id,channel,start_s,end_s,label")
    parser.add_argument("--subject-id", required=True, help="pseudonymous subject ID")
    parser.add_argument("--channel", choices=("drowsiness", "distraction"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--drop-missing", action="store_true", help="omit rows with missing feature values")
    args = parser.parse_args()
    feature_names = (
        ("eye_closure_fraction", "perclos", "yawn_candidate", "head_deviation")
        if args.channel == "drowsiness"
        else ("likely_phone_interaction",)
    )
    try:
        intervals = _read_intervals(Path(args.annotations), args.subject_id, args.channel)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        unlabeled = 0
        missing = 0
        fields = ["subject_id", "channel", "time_s", "label", *feature_names]
        with Path(args.observations_jsonl).open("r", encoding="utf-8") as source, output.open("x", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "observation":
                    continue
                timestamp = row.get("run_time_s")
                if timestamp is None:
                    continue
                timestamp = float(timestamp)
                labels = {label for start, end, label in intervals if start <= timestamp <= end}
                if len(labels) != 1:
                    unlabeled += 1
                    continue
                features = row.get("features", {})
                values = {name: features.get(name) for name in feature_names}
                has_missing = any(value is None for value in values.values())
                if has_missing:
                    missing += 1
                    if args.drop_missing:
                        continue
                writer.writerow({
                    "subject_id": args.subject_id,
                    "channel": args.channel,
                    "time_s": timestamp,
                    "label": next(iter(labels)),
                    **values,
                })
                written += 1
        print(json.dumps({"rows_written": written, "unlabeled_rows_skipped": unlabeled, "rows_with_missing_features": missing, "output": str(output)}, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"feature extraction: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
