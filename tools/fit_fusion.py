from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path, feature_names: list[str], subject_column: str, label_column: str):
    import pandas as pd
    import numpy as np

    frame = pd.read_csv(path)
    required = set(feature_names + [subject_column, label_column])
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
    if frame[subject_column].isna().any() or frame[label_column].isna().any():
        raise ValueError(f"{path} contains missing subject IDs or labels")
    labels = frame[label_column].astype(int)
    if not set(labels.unique()).issubset({0, 1}) or len(set(labels.unique())) != 2:
        raise ValueError(f"{path} must contain both binary labels 0 and 1")
    values = frame[feature_names].astype(float)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError(f"{path} features must be finite; encode validity features explicitly")
    return frame, values.to_numpy(), labels.to_numpy()


def _run(args: argparse.Namespace) -> int:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    feature_names = [part.strip() for part in args.features.split(",") if part.strip()]
    if not feature_names or len(feature_names) != len(set(feature_names)):
        raise ValueError("--features must contain a unique comma-separated list")
    train_path = Path(args.train_csv)
    calibration_path = Path(args.calibration_csv)
    train_frame, x_train, y_train = _read(train_path, feature_names, args.subject_column, args.label_column)
    calibration_frame, x_cal, y_cal = _read(calibration_path, feature_names, args.subject_column, args.label_column)
    train_subjects = set(train_frame[args.subject_column].astype(str))
    calibration_subjects = set(calibration_frame[args.subject_column].astype(str))
    overlap = sorted(train_subjects & calibration_subjects)
    if overlap:
        raise ValueError(f"training and calibration subjects overlap ({len(overlap)} IDs)")
    if len(train_subjects) < 2 or len(calibration_subjects) < 2:
        raise ValueError("use at least two subjects in each development partition")

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_cal_scaled = scaler.transform(x_cal)
    estimator = LogisticRegression(C=args.c, max_iter=2000, class_weight="balanced", random_state=args.seed)
    estimator.fit(x_train_scaled, y_train)
    validation_logits = estimator.decision_function(x_cal_scaled).reshape(-1, 1)
    calibrator = LogisticRegression(C=1e6, max_iter=2000, random_state=args.seed)
    calibrator.fit(validation_logits, y_cal)

    model = {
        "schema_version": 1,
        "model_type": "regularized_logistic_with_sigmoid_calibration",
        "channel": args.channel,
        "feature_names": feature_names,
        "means": scaler.mean_.tolist(),
        "scales": [float(value) if value > 0 else 1.0 for value in scaler.scale_],
        "coefficients": estimator.coef_[0].tolist(),
        "intercept": float(estimator.intercept_[0]),
        "calibration_slope": float(calibrator.coef_[0][0]),
        "calibration_intercept": float(calibrator.intercept_[0]),
        "elevated_threshold": args.elevated_threshold,
        "high_threshold": args.high_threshold,
        "created_at": datetime.now().astimezone().isoformat(),
        "training": {
            "regularization_c": args.c,
            "seed": args.seed,
            "training_subject_count": len(train_subjects),
            "calibration_subject_count": len(calibration_subjects),
            "training_rows": len(y_train),
            "calibration_rows": len(y_cal),
            "training_csv_sha256": _hash(train_path),
            "calibration_csv_sha256": _hash(calibration_path),
            "held_out_test_used": False,
        },
        "claim_limit": "Research artifact; requires frozen subject-disjoint evaluation before use.",
    }
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite fusion model: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(model, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(output), "training_subjects": len(train_subjects), "calibration_subjects": len(calibration_subjects), "test_set_used": False}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit/calibrate a subject-disjoint logistic fusion artifact")
    parser.add_argument("--channel", choices=("drowsiness", "distraction"), required=True)
    parser.add_argument("--train-csv", required=True)
    parser.add_argument("--calibration-csv", required=True)
    parser.add_argument("--features", required=True, help="comma-separated feature columns in model order")
    parser.add_argument("--subject-column", default="subject_id")
    parser.add_argument("--label-column", default="label")
    parser.add_argument("--c", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--elevated-threshold", type=float, default=0.5)
    parser.add_argument("--high-threshold", type=float, default=0.8)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.c <= 0 or not 0 <= args.elevated_threshold < args.high_threshold <= 1:
        parser.error("invalid regularization or decision thresholds")
    try:
        return _run(args)
    except (ImportError, FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f"fusion fit: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
