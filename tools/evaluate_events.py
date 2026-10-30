from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _key(row: dict[str, str]) -> tuple[str, str]:
    return row["subject_id"], row.get("channel", "drowsiness") or "drowsiness"


def _interval(row: dict[str, str]) -> tuple[float, float]:
    start, end = float(row["start_s"]), float(row["end_s"])
    if end < start:
        raise ValueError("event interval end precedes start")
    return start, end


def _maximum_matching(truths, predictions, tolerance_s: float):
    adjacency = []
    for truth in truths:
        ts, te = _interval(truth)
        candidates = []
        for j, prediction in enumerate(predictions):
            ps, pe = _interval(prediction)
            if ps <= te + tolerance_s and pe >= ts:
                candidates.append((abs(ps - ts), j))
        adjacency.append([j for _, j in sorted(candidates)])

    prediction_to_truth: dict[int, int] = {}

    def augment(truth_index: int, seen: set[int]) -> bool:
        for pred_index in adjacency[truth_index]:
            if pred_index in seen:
                continue
            seen.add(pred_index)
            if pred_index not in prediction_to_truth or augment(prediction_to_truth[pred_index], seen):
                prediction_to_truth[pred_index] = truth_index
                return True
        return False

    for truth_index in range(len(truths)):
        augment(truth_index, set())
    return {truth_index: pred_index for pred_index, truth_index in prediction_to_truth.items()}


def _run(args: argparse.Namespace) -> dict:
    truths = _read_rows(args.truth)
    predictions = _read_rows(args.predictions)
    negatives = _read_rows(args.negative_intervals)
    grouped_truth = defaultdict(list)
    grouped_predictions = defaultdict(list)
    grouped_negative = defaultdict(list)
    for row in truths:
        grouped_truth[_key(row)].append(row)
    for row in predictions:
        grouped_predictions[_key(row)].append(row)
    for row in negatives:
        grouped_negative[_key(row)].append(row)

    all_keys = sorted(set(grouped_truth) | set(grouped_predictions) | set(grouped_negative))
    matches = 0
    late = 0
    missed = 0
    false_alarms = 0
    unclassified = 0
    delays = []
    negative_seconds = 0.0
    subject_metrics = defaultdict(lambda: {"truth": 0, "matched": 0, "late": 0, "missed": 0, "false_alarms": 0, "negative_seconds": 0.0})

    for key in all_keys:
        t_rows = sorted(grouped_truth[key], key=lambda row: _interval(row)[0])
        p_rows = sorted(grouped_predictions[key], key=lambda row: _interval(row)[0])
        n_rows = grouped_negative[key]
        mapping = _maximum_matching(t_rows, p_rows, args.max_delay_s)
        used_predictions = set(mapping.values())
        subject, channel = key
        subject_metrics[subject]["truth"] += len(t_rows)
        for truth_index, truth in enumerate(t_rows):
            if truth_index not in mapping:
                missed += 1
                subject_metrics[subject]["missed"] += 1
                continue
            prediction = p_rows[mapping[truth_index]]
            ps, pe = _interval(prediction)
            ts, te = _interval(truth)
            delay = ps - ts
            delays.append(delay)
            matches += 1
            subject_metrics[subject]["matched"] += 1
            if ps > te:
                late += 1
                subject_metrics[subject]["late"] += 1

        key_negative_seconds = 0.0
        for row in n_rows:
            start, end = _interval(row)
            key_negative_seconds += end - start
        negative_seconds += key_negative_seconds
        subject_metrics[subject]["negative_seconds"] += key_negative_seconds
        for pred_index, prediction in enumerate(p_rows):
            if pred_index in used_predictions:
                continue
            ps, _ = _interval(prediction)
            in_negative = any(_interval(interval)[0] <= ps <= _interval(interval)[1] for interval in n_rows)
            if in_negative:
                false_alarms += 1
                subject_metrics[subject]["false_alarms"] += 1
            else:
                unclassified += 1

    result = {
        "schema_version": 1,
        "matching_policy": "maximum-cardinality one-to-one interval match; prediction may start no later than truth end + max_delay_s",
        "max_delay_s": args.max_delay_s,
        "truth_events": len(truths),
        "matched_events": matches,
        "late_events": late,
        "missed_events": missed,
        "event_recall": matches / len(truths) if truths else None,
        "eligible_recall_excluding_late": (matches - late) / len(truths) if truths else None,
        "median_start_delay_s": median(delays) if delays else None,
        "false_alarms_in_annotated_negative_time": false_alarms,
        "annotated_negative_hours": negative_seconds / 3600.0,
        "false_alarms_per_negative_hour": false_alarms / (negative_seconds / 3600.0) if negative_seconds > 0 else None,
        "unclassified_unmatched_predictions": unclassified,
        "subject_metrics": dict(subject_metrics),
        "source_files": {"truth": str(args.truth), "predictions": str(args.predictions), "negative_intervals": str(args.negative_intervals)},
        "claim_limit": "Event counts apply only to declared annotation intervals; unclassified times are not negative evidence.",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate event detections with explicit negative-time coverage")
    parser.add_argument("--truth", required=True, help="CSV: subject_id,channel,start_s,end_s")
    parser.add_argument("--predictions", required=True, help="CSV: subject_id,channel,start_s,end_s")
    parser.add_argument("--negative-intervals", required=True, help="CSV: subject_id,channel,start_s,end_s")
    parser.add_argument("--max-delay-s", type=float, default=2.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.max_delay_s < 0:
        parser.error("--max-delay-s must be nonnegative")
    try:
        result = _run(args)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(json.dumps(result, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"event evaluation: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
