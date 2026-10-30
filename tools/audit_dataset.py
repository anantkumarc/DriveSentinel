from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a local recording manifest without reading video contents")
    parser.add_argument("manifest_csv", help="recording_id,subject_id,path,sha256,license,status,split")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    manifest_path = Path(args.manifest_csv)
    problems = []
    rows = []
    split_by_subject = defaultdict(set)
    required = {"recording_id", "subject_id", "path", "sha256", "license", "status", "split"}
    try:
        with manifest_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing_columns = required - set(reader.fieldnames or ())
            if missing_columns:
                raise ValueError(f"manifest missing columns: {', '.join(sorted(missing_columns))}")
            for line_number, row in enumerate(reader, start=2):
                rows.append(row)
                split_by_subject[row["subject_id"]].add(row["split"])
                path = (root / row["path"]).resolve()
                if not path.is_relative_to(root):
                    problems.append({"line": line_number, "problem": "path escapes dataset root"})
                elif not path.is_file():
                    problems.append({"line": line_number, "problem": "file missing"})
                elif row["sha256"] and _sha256(path).lower() != row["sha256"].lower():
                    problems.append({"line": line_number, "problem": "sha256 mismatch"})
                if not row["license"].strip() or row["license"].strip().lower() in {"unknown", "tbd"}:
                    problems.append({"line": line_number, "problem": "license/provenance unresolved"})
                if row["status"].strip().lower() not in {"approved", "consented", "public-approved"}:
                    problems.append({"line": line_number, "problem": "recording status not approved"})
        for subject, splits in split_by_subject.items():
            if len(splits) > 1:
                problems.append({"subject_id": subject, "problem": "subject appears in multiple splits"})
        result = {
            "schema_version": 1,
            "recordings": len(rows),
            "subjects": len(split_by_subject),
            "split_subject_counts": {split: len({subject for subject, assigned in split_by_subject.items() if split in assigned}) for split in sorted({s for values in split_by_subject.values() for s in values})},
            "problems": problems,
            "clean": not problems,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(json.dumps(result, indent=2))
        return 0 if not problems else 1
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f"dataset audit: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
