from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export deduplicated alert episodes from JSONL run data")
    parser.add_argument("observations_jsonl")
    parser.add_argument("--subject-id", required=True, help="pseudonymous subject or recording ID for evaluation joins")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    episodes: dict[tuple[str, str], dict] = {}
    run_end_s = 0.0
    try:
        with Path(args.observations_jsonl).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("run_time_s") is not None:
                    run_end_s = max(run_end_s, float(row["run_time_s"]))
                if row.get("type") != "alert_transition" or not row.get("episode_id"):
                    continue
                key = (row["channel"], row["episode_id"])
                episode = episodes.setdefault(key, {
                    "subject_id": args.subject_id,
                    "channel": row["channel"],
                    "episode_id": row["episode_id"],
                    "start_s": row.get("run_time_s"),
                    "end_s": "",
                    "level": row.get("level"),
                    "reasons": row.get("reasons", ""),
                })
                level = row.get("level")
                priority = {"none": 0, "elevated": 1, "high": 2}
                if priority.get(level, 0) > priority.get(episode.get("level"), 0):
                    episode["level"] = level
                if isinstance(row.get("reasons"), list):
                    known = episode["reasons"] if isinstance(episode["reasons"], list) else []
                    episode["reasons"] = list(dict.fromkeys([*known, *row["reasons"]]))
                if row.get("level") == "none":
                    episode["end_s"] = row.get("run_time_s")
        for episode in episodes.values():
            if episode["end_s"] == "":
                episode["end_s"] = run_end_s
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", newline="", encoding="utf-8") as handle:
            fields = ("subject_id", "channel", "episode_id", "start_s", "end_s", "level", "reasons")
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in episodes.values():
                item["reasons"] = ";".join(item["reasons"]) if isinstance(item["reasons"], list) else item["reasons"]
                writer.writerow(item)
        print(f"Exported {len(episodes)} alert episodes to {output}")
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"alert extraction: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
