from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from pathlib import Path


def _read(path: str) -> dict[str, float]:
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        output = {}
        for row in rows:
            subject = row["subject_id"]
            if subject in output:
                raise ValueError(f"duplicate subject row in {path}: {subject}")
            output[subject] = float(row["value"])
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Paired subject bootstrap for two policies or models")
    parser.add_argument("--a", required=True, help="CSV with subject_id,value")
    parser.add_argument("--b", required=True)
    parser.add_argument("--metric", default="metric_difference")
    parser.add_argument("--replicates", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.replicates < 100:
        parser.error("use at least 100 bootstrap replicates")
    try:
        a = _read(args.a)
        b = _read(args.b)
        if set(a) != set(b) or len(a) < 2:
            raise ValueError("paired files must contain the same two or more subjects")
        subjects = sorted(a)
        differences = {subject: b[subject] - a[subject] for subject in subjects}
        rng = random.Random(args.seed)
        values = []
        for _ in range(args.replicates):
            sample = [rng.choice(subjects) for _ in subjects]
            values.append(statistics.mean(differences[subject] for subject in sample))
        values.sort()
        low = values[int(0.025 * (len(values) - 1))]
        high = values[int(0.975 * (len(values) - 1))]
        result = {
            "schema_version": 1,
            "metric": args.metric,
            "difference_definition": "B - A",
            "subjects": len(subjects),
            "replicates": args.replicates,
            "seed": args.seed,
            "mean_paired_difference": statistics.mean(differences.values()),
            "paired_subject_bootstrap_95_percentile_interval": [low, high],
            "subject_differences": differences,
            "claim_limit": "The interval reflects subject resampling only; it does not repair biased sampling or labels.",
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(json.dumps(result, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"subject bootstrap: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
