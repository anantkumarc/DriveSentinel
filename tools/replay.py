from __future__ import annotations

import argparse
import json
from pathlib import Path

from dms.detectors.eyes import EyeObservation, EyeState
from dms.temporal.eye_closure import EyeClosureTimer
from dms.temporal.windows import PerclosWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministically replay cached eye observations without camera/model assets")
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--critical-closure-s", type=float, default=1.5)
    parser.add_argument("--max-gap-s", type=float, default=0.25)
    parser.add_argument("--window-s", type=float, default=60.0)
    parser.add_argument("--minimum-coverage", type=float, default=0.5)
    args = parser.parse_args()
    if min(args.critical_closure_s, args.max_gap_s, args.window_s) <= 0 or not 0 <= args.minimum_coverage <= 1:
        parser.error("invalid timing or coverage settings")
    try:
        timer = EyeClosureTimer(args.critical_closure_s, args.max_gap_s)
        perclos = PerclosWindow(args.window_s, args.max_gap_s, args.minimum_coverage)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with Path(args.observations).open("r", encoding="utf-8") as source, output.open("x", encoding="utf-8") as destination:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "observation":
                    continue
                timestamp = float(row.get("run_time_s", row["source_time_s"]))
                try:
                    state = EyeState(row["eye_state"])
                except (ValueError, KeyError):
                    state = EyeState.UNKNOWN
                observation = EyeObservation(state, row.get("ear"), row.get("left_ear"), row.get("right_ear"), row.get("observation_reason"))
                closure = timer.update(timestamp, observation)
                window = perclos.update(timestamp, state)
                result = {
                    "type": "replay_observation",
                    "run_time_s": timestamp,
                    "eye_state": closure.state.value,
                    "closure_duration_s": closure.observed_duration_s,
                    "critical_closure_candidate": closure.prolonged_candidate,
                    "critical_event_id": closure.event_id,
                    "perclos": window.perclos,
                    "perclos_coverage": window.coverage,
                }
                destination.write(json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n")
                count += 1
        print(json.dumps({"replayed_observations": count, "output": str(output)}, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"replay: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
