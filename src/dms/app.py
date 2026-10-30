"""Local, metadata-only driver-monitoring research application."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dms.alerts.audio import AudioAlertManager, PlaybackState
from dms.alerts.states import AlertLevel, AlertState, AlertStateMachine
from dms.capture.backend import CaptureBackend, CaptureSource
from dms.capture.packets import CaptureStatus, FramePacket
from dms.detectors.eyes import EyeDetector, EyeObservation, EyeState
from dms.detectors.eye_calibration import (
    EyeCalibrationSnapshot,
    EyeCalibrationState,
    EyeThresholdCalibrator,
)
from dms.detectors.face_landmarks import FaceLandmarkObservation, FaceLandmarksDetector
from dms.detectors.hands import HandLandmarksDetector, HandObservation
from dms.detectors.head_pose import CameraCalibration, HeadPoseEstimator, HeadPoseObservation
from dms.detectors.mouth import MouthCandidateDecision, MouthDetector, MouthObservation, MouthState, MouthCandidateTracker
from dms.detectors.phone_boxes import MediaPipePhoneDetector, OnnxPhoneDetector, PhoneDetection
from dms.detectors.phone_interaction import PhoneInteractionEstimator, PhoneInteractionObservation
from dms.fusion.base import RiskDecision, RiskState
from dms.fusion.engine import RiskFusionEngine, FusionOutput
from dms.health.watchdog import CaptureWatchdog
from dms.model_assets import (
    ModelCheck,
    ModelIntegrityError,
    ModelManagementError,
    check_line,
    configured_model_requirements,
    download_missing_models,
    inspect_configured_models,
    registered_model_requirements,
    required_model_failure,
    verify_managed_model,
)
from dms.telemetry.logger import JsonlWriter
from dms.temporal.eye_closure import ClosureDecision, EyeClosureTimer
from dms.temporal.head_distraction import (
    DOWNWARD_HEAD_THRESHOLD_SECONDS,
    HEAD_TURN_THRESHOLD_SECONDS,
    HeadDistractionSnapshot,
    HeadDistractionTracker,
)
from dms.temporal.windows import PerclosWindow, WindowFeature

_HEAD_DISTRACTION_REASONS = frozenset(
    {"sustained_left_turn", "sustained_right_turn", "sustained_downward_look"}
)


def _load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is missing; install the project runtime requirements") from exc
    if not path.is_file():
        raise FileNotFoundError(f"configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("configuration must be a YAML mapping with schema_version: 1")
    for section in ("camera", "face", "eyes", "ui"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"configuration section '{section}' must be a mapping")
    return config


def _section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"configuration section '{name}' must be a mapping")
    return value


def _parse_source(source: str | int) -> CaptureSource:
    if isinstance(source, int):
        return source
    if source.isdecimal():
        return int(source)
    return source


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dependency_versions() -> dict[str, str | None]:
    distributions = (
        "driver-monitoring-prototype",
        "mediapipe",
        "opencv-contrib-python",
        "PyYAML",
        "onnxruntime",
        "psutil",
    )
    result: dict[str, str | None] = {}
    for distribution in distributions:
        try:
            result[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            result[distribution] = None
    return result


def _make_run_dir(requested: str | None) -> Path:
    if requested:
        run_dir = Path(requested).expanduser()
    else:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        run_dir = Path("runs") / f"full_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _asset_record(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    result: dict[str, Any] = {"path": str(path)}
    if path.is_file():
        result["sha256"] = _sha256_file(path)
        result["present"] = True
    else:
        result["sha256"] = None
        result["present"] = False
    return result


def _write_manifest(
    run_dir: Path,
    *,
    config_path: Path,
    config: dict[str, Any],
    capture_info: Any,
    requested_source: CaptureSource,
    assets: Mapping[str, Path | None],
    detector_status: Mapping[str, str],
    audio_status: str,
) -> None:
    face_cfg = config["face"]
    camera_cfg = _section(config, "camera")
    camera_label = camera_cfg.get("label")
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "application_version": "0.1.0",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "dependencies": _dependency_versions(),
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "assets": {name: _asset_record(path) for name, path in assets.items()},
        "capture": {
            "source_kind": capture_info.source_kind,
            "source_id": capture_info.source_id,
            "width": capture_info.width,
            "height": capture_info.height,
            "reported_fps": capture_info.reported_fps,
            "fps_is_reported": capture_info.fps_is_reported,
            "backend": capture_info.backend,
            "configured_device_label": camera_label,
            "device_label_source": "operator_configured" if camera_label else "not_recorded",
            "requested_source": str(requested_source),
            "requested_width": camera_cfg.get("width"),
            "requested_height": camera_cfg.get("height"),
        },
        "channels": dict(detector_status),
        "behavior": {
            "ear_closed_threshold": config["eyes"]["ear_closed_threshold"],
            "critical_closure_seconds": config["eyes"]["critical_closure_seconds"],
            "max_sample_gap_seconds": config["eyes"]["max_sample_gap_seconds"],
            "eye_threshold_mode": _section(config, "eyes").get("threshold_mode", "fixed"),
            "personal_calibration_requires_operator_start": True,
            "thresholds_validated": False,
            "fusion_method": _section(config, "fusion").get("method", "rules"),
            "head_distraction": _section(config, "head_distraction"),
            "face_detection_confidence_gate": _section(config, "head_distraction").get(
                "minimum_detection_confidence",
                face_cfg.get("min_face_detection_confidence", 0.5),
            ),
        },
        "alerts": {
            "audio_status": audio_status,
            "audio_engine": "sounddevice continuous piezo buzzer",
            "buzzer_frequency_hz": _section(config, "alerts").get("buzzer_frequency_hz", 2400.0),
            "buzzer_sample_rate_hz": _section(config, "alerts").get("buzzer_sample_rate_hz", 48000),
            "buzzer_volume": _section(config, "alerts").get("buzzer_volume", 0.9),
        },
        "privacy": {
            "raw_video_saved": False,
            "audio_saved": False,
            "face_identity_saved": False,
            "landmark_coordinates_saved": False,
            "phone_or_hand_boxes_saved": False,
        },
        "run_directory": str(run_dir.resolve()),
        "model_provenance_recorded": False,
        "camera_calibration_available": bool(
            assets.get("camera_calibration") and assets["camera_calibration"].is_file()
        ),
    }
    with (run_dir / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, allow_nan=False)
        handle.write("\n")


def _rounded(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _decision_record(decision: RiskDecision) -> dict[str, Any]:
    return {
        "state": decision.state.value,
        "score": _rounded(decision.score),
        "strategy": decision.strategy,
        "reasons": list(decision.reasons),
        "supporting": list(decision.supporting),
        "unavailable": list(decision.unavailable),
        "critical_bypass": decision.critical_bypass,
    }


def _alert_record(alert: AlertState) -> dict[str, Any]:
    return {
        "level": alert.level.name.lower(),
        "episode_id": alert.episode_id,
        "reasons": list(alert.reasons),
        "evidence_state": alert.evidence_state.value,
        "evidence_unknown": alert.evidence_unknown,
        "notify": alert.notify,
        "notification_reason": alert.notification_reason,
    }


def _log_alert_transition(
    logger: JsonlWriter,
    previous: AlertState,
    current: AlertState,
    run_time_s: float,
) -> None:
    if previous.level == current.level and previous.episode_id == current.episode_id:
        return
    logger.write(
        {
            "type": "alert_transition",
            "run_time_s": _rounded(run_time_s, 4),
            "channel": current.channel,
            "episode_id": current.episode_id or previous.episode_id,
            "level": current.level.name.lower(),
            "reasons": list(current.reasons or previous.reasons),
            "evidence_state": current.evidence_state.value,
        }
    )


def _log_head_distraction_transition(
    logger: JsonlWriter,
    previous: HeadDistractionSnapshot,
    current: HeadDistractionSnapshot,
    run_time_s: float,
) -> None:
    previous_ids = (previous.left_episode_id, previous.right_episode_id, previous.downward_episode_id)
    current_ids = (current.left_episode_id, current.right_episode_id, current.downward_episode_id)
    if previous_ids == current_ids:
        return
    logger.write({
        "type": "head_distraction_transition",
        "run_time_s": _rounded(run_time_s, 4),
        "active": current.active,
        "direction": current.direction.value,
        "reasons": list(current.active_reasons),
        "episode_ids": [value for value in current_ids if value is not None],
        "yaw_deg": _rounded(current.yaw_deg, 3),
        "pitch_deg": _rounded(current.pitch_deg, 3),
        "roll_deg": _rounded(current.roll_deg, 3),
    })


def _unknown_face(width: int, height: int, timestamp_ms: int, reason: str) -> FaceLandmarkObservation:
    return FaceLandmarkObservation(width, height, timestamp_ms, None, 0, reason)


def _log_channel_health(
    logger: JsonlWriter,
    previous: dict[str, str],
    channel: str,
    state: str,
    reason: str | None,
    run_time_s: float,
) -> None:
    if previous.get(channel) == state:
        return
    logger.write({
        "type": "channel_health_transition",
        "run_time_s": _rounded(run_time_s, 4),
        "channel": channel,
        "health": state,
        "reason": reason,
    })
    previous[channel] = state


def _start_eye_calibration(
    calibrator: EyeThresholdCalibrator,
    logger: JsonlWriter,
    run_time_s: float,
    threshold_mode: str,
) -> EyeCalibrationSnapshot:
    snapshot = calibrator.start(run_time_s)
    logger.write({
        "type": "eye_calibration_transition",
        "run_time_s": _rounded(run_time_s, 4),
        "state": snapshot.state.value,
        "threshold_mode": threshold_mode,
        "sample_count": 0,
        "reason": "operator_started_calibration",
    })
    return snapshot


def _cancel_eye_calibration(
    calibrator: EyeThresholdCalibrator,
    logger: JsonlWriter,
    run_time_s: float,
    threshold_mode: str,
) -> EyeCalibrationSnapshot:
    snapshot = calibrator.cancel(run_time_s)
    logger.write({
        "type": "eye_calibration_transition",
        "run_time_s": _rounded(run_time_s, 4),
        "state": snapshot.state.value,
        "threshold_mode": threshold_mode,
        "sample_count": snapshot.sample_count,
        "reason": snapshot.reason,
    })
    return snapshot


def _init_optional_detectors(
    config: Mapping[str, Any], root: Path
) -> tuple[
    MouthDetector,
    MouthCandidateTracker,
    HeadPoseEstimator,
    OnnxPhoneDetector | MediaPipePhoneDetector | None,
    HandLandmarksDetector | None,
    PhoneInteractionEstimator,
    dict[str, Path | None],
    dict[str, str],
]:
    mouth_cfg = _section(config, "mouth")
    pose_cfg = _section(config, "head_pose")
    phone_cfg = _section(config, "phone")
    hand_cfg = _section(config, "hands")
    interaction_cfg = _section(config, "interaction")
    assets: dict[str, Path | None] = {"hand": None, "phone": None, "camera_calibration": None, "head_neutral": None}
    status: dict[str, str] = {}

    mouth = MouthDetector(
        float(mouth_cfg.get("open_ratio_threshold", 0.55)),
        float(mouth_cfg.get("min_mouth_width_px", 12)),
    )
    mouth_tracker = MouthCandidateTracker(
        float(mouth_cfg.get("candidate_after_seconds", 1.0)),
        float(mouth_cfg.get("max_sample_gap_seconds", 0.25)),
    )
    status["mouth"] = "ready" if mouth_cfg.get("enabled", False) else "disabled"

    calibration: CameraCalibration | None = None
    neutral_rotation = None
    if pose_cfg.get("enabled", False):
        calibration_path = _resolve(root, pose_cfg.get("camera_calibration", "configs/camera_calibration.json"))
        neutral_path = _resolve(root, pose_cfg.get("neutral_reference", "configs/head_pose_neutral.json"))
        assets["camera_calibration"] = calibration_path
        assets["head_neutral"] = neutral_path
        try:
            if calibration_path.is_file():
                calibration = CameraCalibration.load(calibration_path)
            if neutral_path.is_file():
                neutral_rotation = HeadPoseEstimator.load_neutral(neutral_path)
        except (FileNotFoundError, OSError, ValueError, KeyError) as exc:
            status["head_pose"] = f"auto_neutral_fallback:{type(exc).__name__}:{exc}"
    else:
        status["head_pose"] = "disabled"
    pose = HeadPoseEstimator(
        calibration,
        neutral_rotation,
        float(pose_cfg.get("max_reprojection_error_px", 12.0)),
        float(pose_cfg.get("neutral_calibration_seconds", 2.0)),
    )
    if pose_cfg.get("enabled", False):
        status["head_pose"] = "ready" if pose.neutral_ready else "auto_neutral_calibration_pending"

    phone: OnnxPhoneDetector | MediaPipePhoneDetector | None = None
    if phone_cfg.get("enabled", False):
        backend = str(phone_cfg.get("backend", "onnx")).casefold()
        default_model = "models/efficientdet_lite0.tflite" if backend == "mediapipe" else "models/phone_detector.onnx"
        phone_path = _resolve(root, phone_cfg.get("model", default_model))
        assets["phone"] = phone_path
        try:
            verify_managed_model(phone_path, root)
            if backend == "mediapipe":
                phone = MediaPipePhoneDetector(
                    phone_path,
                    score_threshold=float(phone_cfg.get("score_threshold", 0.30)),
                )
            elif backend == "onnx":
                phone = OnnxPhoneDetector(
                    phone_path,
                    output_format=str(phone_cfg.get("output_format", "yolov8")),
                    image_size=int(phone_cfg.get("image_size", 640)),
                    phone_class_id=int(phone_cfg.get("phone_class_id", 67)),
                    score_threshold=float(phone_cfg.get("score_threshold", 0.25)),
                    nms_threshold=float(phone_cfg.get("nms_threshold", 0.45)),
                )
            else:
                raise ValueError("phone backend must be onnx or mediapipe")
            status["phone"] = "ready"
        except (FileNotFoundError, ImportError, OSError, ValueError, RuntimeError) as exc:
            status["phone"] = f"unavailable:{type(exc).__name__}:{exc}"
    else:
        status["phone"] = "disabled"

    hands: HandLandmarksDetector | None = None
    if hand_cfg.get("enabled", False):
        hand_path = _resolve(root, hand_cfg.get("model", "models/hand_landmarker.task"))
        assets["hand"] = hand_path
        try:
            verify_managed_model(hand_path, root)
            hands = HandLandmarksDetector(hand_path)
            status["hands"] = "ready"
        except (FileNotFoundError, ImportError, OSError, ValueError, RuntimeError) as exc:
            status["hands"] = f"unavailable:{type(exc).__name__}:{exc}"
    else:
        status["hands"] = "disabled"

    roi = interaction_cfg.get("driver_roi")
    interaction = PhoneInteractionEstimator(
        roi,
        proximity_fraction=float(interaction_cfg.get("proximity_fraction", 0.06)),
        association_validated=bool(interaction_cfg.get("association_validated", False)),
    )
    interaction_inputs_ready = interaction.driver_roi is not None and phone is not None and hands is not None
    if not interaction_inputs_ready:
        status["phone_interaction"] = "unavailable_roi_or_model_missing"
    else:
        status["phone_interaction"] = "candidate_diagnostics_only_not_alarm_input"
    presence_alert_enabled = bool(
        _section(config, "fusion").get("channels", {}).get("alert_on_phone_presence", False)
    )
    if phone is None or interaction.driver_roi is None:
        status["phone_presence"] = "unavailable_roi_or_model_missing"
    else:
        status["phone_presence"] = "alert_on_presence" if presence_alert_enabled else "monitor_only"
    return mouth, mouth_tracker, pose, phone, hands, interaction, assets, status


def _overlay(
    cv2: Any,
    image: Any,
    *,
    capture_health: str,
    eye: EyeObservation,
    closure: ClosureDecision,
    perclos: WindowFeature,
    mouth: MouthCandidateDecision,
    pose: HeadPoseObservation,
    phone: PhoneDetection,
    hands: HandObservation,
    interaction: PhoneInteractionObservation,
    head_distraction: HeadDistractionSnapshot,
    head_turn_threshold_s: float,
    downward_head_threshold_s: float,
    face_tracking_available: bool,
    face_detection_confidence_gate: float,
    audio_status: str,
    audio_playback_state: PlaybackState,
    drowsiness_alert: AlertState,
    distraction_alert: AlertState,
    eye_threshold_mode: str,
    eye_calibration: EyeCalibrationSnapshot,
) -> Any:
    view = image.copy()
    width = view.shape[1]
    color = (0, 210, 0)
    if capture_health != "available" or eye.state is EyeState.UNKNOWN:
        color = (0, 180, 255)
    elif drowsiness_alert.level >= AlertLevel.HIGH or distraction_alert.level >= AlertLevel.HIGH:
        color = (0, 0, 255)
    elif drowsiness_alert.level is AlertLevel.ELEVATED or distraction_alert.level is AlertLevel.ELEVATED:
        color = (0, 165, 255)

    lines = [
        "DRIVER MONITORING RESEARCH PROTOTYPE - NOT SAFETY CERTIFIED",
        f"Capture: {capture_health.upper()} | Eyes: {eye.state.value.upper()} | EAR: {_fmt(eye.ear)}",
        f"Eye threshold: {eye_threshold_mode} | calibration: {eye_calibration.state.value} ({eye_calibration.sample_count} valid samples)",
        f"Calibration: {eye_calibration.elapsed_s:.1f}/{eye_calibration.duration_s:.0f}s | baseline {_fmt(eye_calibration.open_ear)} | closed threshold {_fmt(eye_calibration.closed_threshold)}",
        "C: start/restart calibration (hold eyes open) | X: cancel | Q: quit",
        "Keep head forward for first 2s | N: recalibrate head baseline",
        f"Eye channel: {eye.reason or 'observation available'}",
        f"Eye closure: {_fmt(closure.observed_duration_s, 2)} s | PERCLOS: {_fmt(perclos.perclos)} (coverage {_fmt(perclos.coverage)})",
        f"Drowsiness alert: {drowsiness_alert.level.name.lower()} | evidence {drowsiness_alert.evidence_state.value}",
        f"Mouth: {mouth.state.value} | opening candidate: {'yes' if mouth.candidate_active else 'no'}",
        f"Face tracking: {'AVAILABLE' if face_tracking_available else 'UNAVAILABLE'} | detector gate >= {face_detection_confidence_gate:.2f}; frame score not exposed",
        f"Head pose: {'AVAILABLE' if head_distraction.valid else 'UNKNOWN'} | {pose.reason or 'measured'} | confidence score not exposed",
        f"Head direction: {head_distraction.direction.value.upper()} | relative yaw/pitch/roll: {_fmt(head_distraction.yaw_deg, 1)}/{_fmt(head_distraction.pitch_deg, 1)}/{_fmt(head_distraction.roll_deg, 1)} deg",
        f"Left turn: {head_distraction.left_duration_s:.1f}/{head_turn_threshold_s:.1f}s | {'HIGH' if head_distraction.left_active else 'tracking'}",
        f"Right turn: {head_distraction.right_duration_s:.1f}/{head_turn_threshold_s:.1f}s | {'HIGH' if head_distraction.right_active else 'tracking'}",
        f"Downward look: {head_distraction.downward_duration_s:.1f}/{downward_head_threshold_s:.1f}s | {'HIGH' if head_distraction.downward_active else 'tracking'}",
        f"Phone: {'PRESENT' if interaction.phone_present_in_roi else ('none' if interaction.phone_presence_valid else 'unknown')} | presence warning: {'ACTIVE' if interaction.phone_present_in_roi else ('armed' if interaction.phone_presence_valid else 'unknown')} | phone/hand candidate: {'diagnostic only' if interaction.candidate else 'none'}",
        f"Distraction reason: {', '.join(distraction_alert.reasons or head_distraction.active_reasons) or 'none'}",
        f"Risk: {max(drowsiness_alert.level, distraction_alert.level).name.upper()} | audio: {audio_status} / {audio_playback_state.value}",
    ]
    panel_height = min(view.shape[0] - 1, 18 + 22 * len(lines))
    cv2.rectangle(view, (0, 0), (min(width - 1, 1200), min(view.shape[0] - 1, panel_height)), (20, 24, 30), -1)
    for index, line in enumerate(lines):
        cv2.putText(view, line, (14, 24 + index * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
    if drowsiness_alert.level >= AlertLevel.HIGH:
        cv2.rectangle(view, (0, 0), (width - 1, view.shape[0] - 1), (0, 0, 255), 5)
    return view


def _fmt(value: float | None, digits: int = 3) -> str:
    return "unknown" if value is None else f"{value:.{digits}f}"


def _evidence_features(fusion: FusionOutput) -> dict[str, float | None]:
    all_evidence = {**fusion.evidence["drowsiness"], **fusion.evidence["distraction"]}
    return {
        name: _rounded(item.value) if item.valid else None
        for name, item in all_evidence.items()
    }


def _log_observation(
    logger: JsonlWriter,
    *,
    packet: FramePacket,
    last_sequence: int,
    run_time_s: float,
    processing_ms: float,
    capture_health: str,
    capture_age_s: float | None,
    eye: EyeObservation,
    eye_threshold_mode: str,
    eye_calibration: EyeCalibrationSnapshot,
    closure: ClosureDecision,
    perclos: WindowFeature,
    mouth_observation: MouthObservation,
    mouth: MouthCandidateDecision,
    pose: HeadPoseObservation,
    phone: PhoneDetection,
    phone_age_s: float | None,
    hands: HandObservation,
    hand_age_s: float | None,
    interaction: PhoneInteractionObservation,
    head_distraction: HeadDistractionSnapshot,
    face_tracking_available: bool,
    face_detection_confidence_gate: float,
    audio_status: str,
    audio_playback_state: PlaybackState,
    fusion: FusionOutput,
    drowsiness_alert: AlertState,
    distraction_alert: AlertState,
) -> None:
    logger.write(
        {
            "type": "observation",
            "sequence": packet.sequence,
            "skipped_capture_frames": max(0, packet.sequence - last_sequence - 1),
            "source_time_s": _rounded(packet.source_time_s),
            "run_time_s": _rounded(run_time_s, 4),
            "capture_age_ms": _rounded(max(0.0, (time.monotonic_ns() - packet.captured_monotonic_ns) / 1_000_000.0), 3),
            "processing_ms": _rounded(processing_ms, 3),
            "capture_health": capture_health,
            "capture_age_s": _rounded(capture_age_s, 4),
            "eye_state": eye.state.value,
            "ear": _rounded(eye.ear),
            "left_ear": _rounded(eye.left_ear),
            "right_ear": _rounded(eye.right_ear),
            "eye_threshold_mode": eye_threshold_mode,
            "eye_calibration_state": eye_calibration.state.value,
            "observation_reason": eye.reason,
            "observed_closure_duration_s": _rounded(closure.observed_duration_s, 4),
            "prolonged_closure_candidate": closure.prolonged_candidate,
            "closure_event_id": closure.event_id,
            "candidate_reason": "bilateral_ear_threshold_and_duration" if closure.prolonged_candidate else None,
            "perclos": _rounded(perclos.perclos),
            "perclos_coverage": _rounded(perclos.coverage),
            "perclos_closed_seconds": _rounded(perclos.closed_seconds, 4),
            "perclos_observed_seconds": _rounded(perclos.observed_seconds, 4),
            "mouth_state": mouth_observation.state.value,
            "mouth_opening_ratio": _rounded(mouth_observation.opening_ratio),
            "mouth_reason": mouth_observation.reason,
            "yawn_candidate": mouth.candidate_active,
            "yawn_candidate_started": mouth.candidate_started,
            "yawn_candidate_ended": mouth.candidate_ended,
            "yawn_event_id": mouth.event_id,
            "head_pose_valid": pose.valid,
            "head_pose_reason": pose.reason,
            "head_relative_yaw_deg": _rounded(pose.relative_yaw_deg),
            "head_relative_pitch_deg": _rounded(pose.relative_pitch_deg),
            "head_relative_roll_deg": _rounded(pose.relative_roll_deg),
            "head_reprojection_error_px": _rounded(pose.reprojection_error_px),
            "face_tracking_available": face_tracking_available,
            # MediaPipe's Python result has no per-frame detector score. Keep
            # that unavailable rather than presenting its configured gate as
            # a measured confidence value.
            "face_detection_confidence": None,
            "face_detection_confidence_reported": False,
            "face_detection_confidence_gate": _rounded(face_detection_confidence_gate, 3),
            "head_detection_valid": head_distraction.valid,
            "head_direction": head_distraction.direction.value,
            "head_left_duration_s": _rounded(head_distraction.left_duration_s, 4),
            "head_right_duration_s": _rounded(head_distraction.right_duration_s, 4),
            "head_down_duration_s": _rounded(head_distraction.downward_duration_s, 4),
            "head_left_active": head_distraction.left_active,
            "head_right_active": head_distraction.right_active,
            "head_down_active": head_distraction.downward_active,
            "head_distraction_reasons": list(head_distraction.active_reasons),
            "head_episode_ids": [value for value in (
                head_distraction.left_episode_id,
                head_distraction.right_episode_id,
                head_distraction.downward_episode_id,
            ) if value is not None],
            "phone_valid": phone.valid,
            "phone_count": len(phone.boxes) if phone.valid else None,
            "phone_reason": phone.reason,
            "phone_age_s": _rounded(phone_age_s, 4),
            "hands_valid": hands.valid,
            "hand_count": len(hands.boxes) if hands.valid else None,
            "hands_reason": hands.reason,
            "hand_age_s": _rounded(hand_age_s, 4),
            "phone_interaction_state": interaction.state,
            "phone_interaction_candidate": interaction.candidate,
            "phone_interaction_validated": interaction.validated,
            "phone_interaction_reason": interaction.reason,
            "phone_present_in_roi": interaction.phone_present_in_roi,
            "phone_presence_valid": interaction.phone_presence_valid,
            "drowsiness": _decision_record(fusion.drowsiness),
            "distraction": _decision_record(fusion.distraction),
            "drowsiness_alert": _alert_record(drowsiness_alert),
            "distraction_alert": _alert_record(distraction_alert),
            "risk_level": max(drowsiness_alert.level, distraction_alert.level).name.lower(),
            "audio_status": audio_status,
            "audio_playback_state": audio_playback_state.value,
            "features": _evidence_features(fusion),
        }
    )


def _run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = _load_config(config_path)
    root = config_path.parent.parent
    model_checks = inspect_configured_models(config, root)
    face_check = next((item for item in model_checks if item.requirement.key == "face"), None)
    if face_check is None:
        raise RuntimeError("configuration did not produce a required Face Landmarker model entry")
    if face_check.status == "missing":
        if face_check.requirement.asset is None:
            raise FileNotFoundError(
                f"Required face model is missing: {face_check.requirement.path}. This custom model path "
                "has no project-approved download source. Install a compatible model there, or configure "
                "models/face_landmarker.task and run `drive --download-models`."
            )
        raise FileNotFoundError(
            f"Required face model is missing: {face_check.requirement.path}. "
            "Run `drive --download-models`, then `drive --check-models`."
        )
    if face_check.status == "invalid":
        raise ModelIntegrityError(
            f"{check_line(face_check, root)}. The existing file was left untouched. "
            "Move or remove it, then run `drive --download-models` to fetch the pinned model."
        )
    _warn_about_optional_models(model_checks, root)
    camera_cfg = config["camera"]
    face_cfg = config["face"]
    head_distraction_cfg = _section(config, "head_distraction")
    face_detection_confidence_gate = float(
        head_distraction_cfg.get(
            "minimum_detection_confidence",
            face_cfg.get("min_face_detection_confidence", 0.5),
        )
    )
    eyes_cfg = dict(config["eyes"])
    eye_threshold_mode = args.eye_threshold_mode or eyes_cfg.get("threshold_mode", "fixed")
    if eye_threshold_mode not in {"fixed", "personalized", "personalized-quality"}:
        raise ValueError("eye threshold mode must be fixed, personalized, or personalized-quality")
    preview_will_show = bool(_section(config, "ui").get("show_preview", True)) and not args.headless
    if eye_threshold_mode != "fixed" and not preview_will_show and not args.calibrate_eyes_at_start:
        raise ValueError(
            "personalized eye modes need the preview calibration key; enable preview or pass --calibrate-eyes-at-start"
        )
    eyes_cfg["threshold_mode"] = eye_threshold_mode
    config = dict(config)
    config["eyes"] = eyes_cfg
    eye_calibrator = EyeThresholdCalibrator(
        duration_s=float(eyes_cfg.get("personal_calibration_duration_seconds", 20.0)),
        closed_ratio=float(eyes_cfg.get("personal_calibration_closed_ratio", 0.70)),
        min_samples=int(eyes_cfg.get("personal_calibration_min_samples", 50)),
        min_open_ear=float(eyes_cfg.get("personal_calibration_min_open_ear", 0.12)),
        max_open_ear=float(eyes_cfg.get("personal_calibration_max_open_ear", 0.55)),
        max_relative_mad=float(eyes_cfg.get("personal_calibration_max_relative_mad", 0.20)),
        max_relative_asymmetry=float(eyes_cfg.get("quality_max_relative_asymmetry", 0.40)),
    )
    fusion_cfg = _section(config, "fusion")
    alert_cfg = _section(config, "alerts")
    ui_cfg = config["ui"]

    source = _parse_source(args.source if args.source is not None else camera_cfg["source"])
    face_model = _resolve(root, face_cfg["model"])
    mouth_detector, mouth_tracker, pose_estimator, phone_detector, hand_detector, interaction_estimator, assets, detector_status = _init_optional_detectors(config, root)
    assets["face"] = face_model
    face_detector = FaceLandmarksDetector(
        face_model,
        min_detection_confidence=face_detection_confidence_gate,
        min_presence_confidence=float(face_cfg.get("min_face_presence_confidence", 0.5)),
        min_tracking_confidence=float(face_cfg.get("min_face_tracking_confidence", 0.5)),
    )
    eye_detector = EyeDetector(
        ear_closed_threshold=float(eyes_cfg["ear_closed_threshold"]),
        min_eye_width_px=float(face_cfg["min_eye_width_px"]),
    )
    detector_status["face"] = "ready"
    detector_status["eyes"] = "ready" if eye_threshold_mode == "fixed" else "awaiting_operator_calibration"
    detector_status["head_distraction"] = (
        "enabled_requires_neutral_pose" if head_distraction_cfg.get("enabled", False) else "disabled"
    )

    try:
        run_dir = _make_run_dir(args.run_dir)
    except Exception:
        face_detector.close()
        if hand_detector is not None:
            hand_detector.close()
        if phone_detector is not None:
            phone_detector.close()
        raise

    capture = CaptureBackend(source, width=int(camera_cfg["width"]), height=int(camera_cfg["height"]))
    watchdog = CaptureWatchdog(unavailable_after_s=float(eyes_cfg.get("capture_unavailable_after_seconds", 1.0)))
    eye_timer = EyeClosureTimer(
        critical_seconds=float(eyes_cfg["critical_closure_seconds"]),
        max_sample_gap_seconds=float(eyes_cfg["max_sample_gap_seconds"]),
    )
    perclos_window = PerclosWindow(
        window_s=float(eyes_cfg.get("perclos_window_seconds", 60.0)),
        max_gap_s=float(eyes_cfg["max_sample_gap_seconds"]),
        min_coverage=float(eyes_cfg.get("perclos_min_coverage", 0.5)),
    )
    pose_cfg = _section(config, "head_pose")
    head_distraction_tracker = HeadDistractionTracker(
        left_yaw_threshold_deg=float(head_distraction_cfg.get("left_yaw_threshold_deg", 20.0)),
        right_yaw_threshold_deg=float(head_distraction_cfg.get("right_yaw_threshold_deg", 20.0)),
        downward_pitch_threshold_deg=float(head_distraction_cfg.get("downward_pitch_threshold_deg", 18.0)),
        head_turn_duration_s=float(
            head_distraction_cfg.get("head_turn_threshold_seconds", HEAD_TURN_THRESHOLD_SECONDS)
        ),
        downward_duration_s=float(
            head_distraction_cfg.get("downward_head_threshold_seconds", DOWNWARD_HEAD_THRESHOLD_SECONDS)
        ),
        recovery_duration_s=float(head_distraction_cfg.get("recovery_duration_seconds", 0.5)),
        max_sample_gap_s=float(head_distraction_cfg.get("max_sample_gap_seconds", 0.5)),
        left_yaw_sign=int(head_distraction_cfg.get("left_yaw_sign", -1)),
        right_yaw_sign=int(head_distraction_cfg.get("right_yaw_sign", 1)),
        downward_pitch_sign=int(head_distraction_cfg.get("downward_pitch_sign", 1)),
        left_yaw_recovery_threshold_deg=float(
            head_distraction_cfg.get("left_yaw_recovery_threshold_deg", 15.0)
        ),
        right_yaw_recovery_threshold_deg=float(
            head_distraction_cfg.get("right_yaw_recovery_threshold_deg", 15.0)
        ),
        downward_pitch_recovery_threshold_deg=float(
            head_distraction_cfg.get("downward_pitch_recovery_threshold_deg", 12.0)
        ),
        smoothing_time_constant_s=float(
            head_distraction_cfg.get("angle_smoothing_time_constant_seconds", 0.15)
        ),
    )
    fusion_cfg = dict(fusion_cfg)
    if args.fusion_method is not None:
        fusion_cfg["method"] = args.fusion_method
    config = dict(config)
    config["fusion"] = fusion_cfg
    fusion_cfg["channels"] = dict(fusion_cfg.get("channels", {}))
    fusion_cfg["channels"].setdefault("head_yaw_limit_deg", pose_cfg.get("yaw_deviation_degrees", 20.0))
    fusion_engine = RiskFusionEngine(fusion_cfg, root)
    detector_status["fusion"] = fusion_engine.method
    for channel, error in fusion_engine.calibration_errors.items():
        detector_status[f"fusion_{channel}"] = f"unavailable:{error}"
    entry_s = float(alert_cfg.get("entry_persistence_seconds", 0.5))
    recovery_s = float(alert_cfg.get("recovery_persistence_seconds", 1.0))
    cooldown_s = float(alert_cfg.get("repeat_cooldown_seconds", 8.0))
    alert_gap_s = float(alert_cfg.get("max_sample_gap_seconds", eyes_cfg["max_sample_gap_seconds"]))
    drowsiness_machine = AlertStateMachine("drowsiness", entry_persistence_s=entry_s, recovery_persistence_s=recovery_s, repeat_cooldown_s=cooldown_s, max_sample_gap_s=alert_gap_s)
    distraction_entry_s = float(alert_cfg.get("distraction_entry_persistence_seconds", 0.0))
    phone_presence_entry_s = float(
        alert_cfg.get("phone_presence_entry_persistence_seconds", 1.0)
    )
    phone_presence_recovery_s = float(
        alert_cfg.get("phone_presence_recovery_persistence_seconds", 0.5)
    )
    distraction_machine = AlertStateMachine(
        "distraction",
        entry_persistence_s=distraction_entry_s,
        recovery_persistence_s=recovery_s,
        repeat_cooldown_s=cooldown_s,
        max_sample_gap_s=alert_gap_s,
        repeat_notifications=False,
    )
    audio_enabled = bool(alert_cfg.get("audio_enabled", False))
    audio = AudioAlertManager(
        enabled=audio_enabled,
        sample_rate_hz=int(alert_cfg.get("buzzer_sample_rate_hz", 48000)),
        frequency_hz=float(alert_cfg.get("buzzer_frequency_hz", 2400.0)),
        volume=float(alert_cfg.get("buzzer_volume", 0.9)),
        blocksize=int(alert_cfg.get("audio_blocksize", 512)),
    )
    detector_status["audio"] = audio.status
    logger: JsonlWriter | None = None
    preview = bool(ui_cfg.get("show_preview", True)) and not args.headless
    start_ns = time.monotonic_ns()
    current_eye_calibration = eye_calibrator.snapshot(0.0)
    source_origin_s: float | None = None
    last_task_timestamp_ms = -1
    last_sequence = 0
    last_packet: FramePacket | None = None
    last_health_log: str | None = None
    channel_health: dict[str, str] = {}
    last_phone_time_s: float | None = None
    last_hand_time_s: float | None = None
    phone_period = max(0.0, float(_section(config, "phone").get("inference_interval_seconds", 0.2)))
    hand_period = max(0.0, float(_section(config, "hands").get("inference_interval_seconds", 0.1)))
    current_phone = PhoneDetection(False, (), "phone_detector_disabled_or_unavailable")
    current_hands = HandObservation(False, (), "hand_detector_disabled_or_unavailable")
    current_eye = EyeObservation(EyeState.UNKNOWN, None, None, None, "waiting_for_frame")
    current_closure = ClosureDecision(EyeState.UNKNOWN, None, False, "waiting_for_frame")
    current_perclos = WindowFeature(None, 0.0, 0.0, 0.0, False)
    current_mouth_observation = MouthObservation(MouthState.UNKNOWN, None, "waiting_for_frame")
    current_mouth = MouthCandidateDecision(MouthState.UNKNOWN, None, False, False, False, None, "waiting_for_frame")
    current_pose = HeadPoseObservation(False, None, None, None, None, None, None, None, None, "waiting_for_frame")
    current_interaction = PhoneInteractionObservation("unknown", False, False, "waiting_for_frame", None)
    current_drowsiness_alert = AlertState("drowsiness", AlertLevel.NONE, None, (), RiskState.UNKNOWN, True, False, None)
    current_distraction_alert = AlertState("distraction", AlertLevel.NONE, None, (), RiskState.UNKNOWN, True, False, None)
    cv2 = None
    resource_process = None
    last_audio_error_logged: str | None = None
    last_audio_state: str | None = None
    current_head_distraction = head_distraction_tracker.update(
        0.0, yaw_deg=None, pitch_deg=None, roll_deg=None, valid=False
    )
    try:
        try:
            import psutil

            resource_process = psutil.Process()
        except ImportError:
            pass
        capture.start()
        packet = capture.read_newer(0, timeout_s=5.0)
        snapshot = capture.snapshot()
        if packet is None:
            raise RuntimeError(f"capture did not produce a frame: {snapshot.error or snapshot.status.value}")
        watchdog.observe_packet(packet)
        last_packet = packet
        if snapshot.info is None:
            raise RuntimeError("capture started without a camera/video metadata record")
        _write_manifest(
            run_dir,
            config_path=config_path,
            config=config,
            capture_info=snapshot.info,
            requested_source=source,
            assets=assets,
            detector_status=detector_status,
            audio_status=audio.status,
        )
        logger = JsonlWriter(run_dir / "observations.jsonl")
        logger.write({"type": "system_status", "run_time_s": 0.0, "channels": detector_status, "audio_status": audio.status, "audio_error": audio.error})
        print(f"Run directory: {run_dir.resolve()}")
        if args.calibrate_eyes_at_start:
            current_eye_calibration = _start_eye_calibration(
                eye_calibrator,
                logger,
                (time.monotonic_ns() - start_ns) / 1_000_000_000.0,
                eye_threshold_mode,
            )

        process_time_origin = time.process_time()
        resource_wall_origin = time.monotonic()
        next_resource_at = resource_wall_origin + 1.0
        last_cpu_time = process_time_origin
        last_resource_wall = resource_wall_origin

        while True:
            now_ns = time.monotonic_ns()
            run_time_s = (now_ns - start_ns) / 1_000_000_000.0
            if args.seconds is not None and run_time_s >= args.seconds:
                break

            if packet is None:
                packet = capture.read_newer(last_sequence, timeout_s=0.1)
                if packet is None:
                    snapshot = capture.snapshot()
                    current_health, age_s = watchdog.health(snapshot)
                    current_head_distraction = head_distraction_tracker.update(
                        run_time_s,
                        yaw_deg=None,
                        pitch_deg=None,
                        roll_deg=None,
                        valid=False,
                    )
                    if current_health != last_health_log:
                        logger.write({
                            "type": "health_transition",
                            "run_time_s": _rounded(run_time_s, 4),
                            "capture_health": current_health,
                            "capture_age_s": _rounded(age_s, 4),
                            "capture_status": snapshot.status.value,
                            "reason": snapshot.error,
                            "behavior_state": "unknown_due_to_no_fresh_observation",
                        })
                        last_health_log = current_health
                    if snapshot.status in {CaptureStatus.EOF, CaptureStatus.UNAVAILABLE, CaptureStatus.FAILED, CaptureStatus.STOPPED}:
                        if snapshot.status is not CaptureStatus.EOF:
                            print(f"Capture ended: {snapshot.error or snapshot.status.value}", file=sys.stderr)
                        break
                    if preview and last_packet is not None:
                        if cv2 is None:
                            import cv2 as _cv2
                            cv2 = _cv2
                        cv2.imshow("Driver Monitoring Research Prototype", _overlay(
                            cv2,
                            last_packet.image,
                            capture_health=current_health,
                            eye=EyeObservation(EyeState.UNKNOWN, None, None, None, "no_fresh_frame"),
                            closure=ClosureDecision(EyeState.UNKNOWN, None, False, "no_fresh_frame"),
                            perclos=WindowFeature(None, 0.0, 0.0, 0.0, False),
                            mouth=MouthCandidateDecision(MouthState.UNKNOWN, None, False, False, False, None, "no_fresh_frame"),
                            pose=HeadPoseObservation(False, None, None, None, None, None, None, None, None, "no_fresh_frame"),
                            phone=PhoneDetection(False, (), "no_fresh_frame"),
                            hands=HandObservation(False, (), "no_fresh_frame"),
                            interaction=PhoneInteractionObservation("unknown", False, False, "no_fresh_frame", None),
                            head_distraction=current_head_distraction,
                            head_turn_threshold_s=head_distraction_tracker.head_turn_duration_s,
                            downward_head_threshold_s=head_distraction_tracker.downward_duration_s,
                            face_tracking_available=False,
                            face_detection_confidence_gate=face_detection_confidence_gate,
                            audio_status=audio.status,
                            audio_playback_state=audio.playback_state,
                            drowsiness_alert=current_drowsiness_alert,
                            distraction_alert=current_distraction_alert,
                            eye_threshold_mode=eye_threshold_mode,
                            eye_calibration=current_eye_calibration,
                        ))
                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q"):
                            break
                        if key in {ord("c"), ord("C")}:
                            current_eye_calibration = _start_eye_calibration(
                                eye_calibrator, logger, run_time_s, eye_threshold_mode
                            )
                        if key in {ord("x"), ord("X")} and eye_calibrator.state is EyeCalibrationState.COLLECTING:
                            current_eye_calibration = _cancel_eye_calibration(
                                eye_calibrator, logger, run_time_s, eye_threshold_mode
                            )
                    continue

            processing_start = time.perf_counter()
            watchdog.observe_packet(packet)
            if source_origin_s is None:
                source_origin_s = packet.source_time_s
            task_timestamp_ms = int(round((packet.source_time_s - source_origin_s) * 1000.0))
            task_timestamp_ms = max(task_timestamp_ms, last_task_timestamp_ms + 1)
            last_task_timestamp_ms = task_timestamp_ms
            run_time_s = (time.monotonic_ns() - start_ns) / 1_000_000_000.0

            try:
                face = face_detector.detect(packet.image, task_timestamp_ms)
            except Exception as exc:
                face = _unknown_face(packet.image.shape[1], packet.image.shape[0], task_timestamp_ms, f"face_detector_error:{type(exc).__name__}")
            raw_eye = eye_detector.observe(face)
            previous_calibration_state = current_eye_calibration.state
            current_eye_calibration = eye_calibrator.update(run_time_s, raw_eye)
            if current_eye_calibration.state is not previous_calibration_state:
                logger.write({
                    "type": "eye_calibration_transition",
                    "run_time_s": _rounded(run_time_s, 4),
                    "state": current_eye_calibration.state.value,
                    "threshold_mode": eye_threshold_mode,
                    "sample_count": current_eye_calibration.sample_count,
                    "open_eye_ear": _rounded(current_eye_calibration.open_ear),
                    "closed_threshold": _rounded(current_eye_calibration.closed_threshold),
                    "relative_mad": _rounded(current_eye_calibration.relative_mad),
                    "reason": current_eye_calibration.reason,
                })

            if eye_threshold_mode == "fixed":
                current_eye = raw_eye
            elif current_eye_calibration.state is EyeCalibrationState.READY:
                current_eye = eye_detector.observe(
                    face,
                    ear_closed_threshold=current_eye_calibration.closed_threshold,
                    max_relative_asymmetry=(
                        eye_calibrator.max_relative_asymmetry
                        if eye_threshold_mode == "personalized-quality"
                        else None
                    ),
                )
            else:
                reason = (
                    "personal_calibration_in_progress"
                    if current_eye_calibration.state is EyeCalibrationState.COLLECTING
                    else "personal_calibration_required"
                )
                current_eye = EyeObservation(
                    EyeState.UNKNOWN,
                    raw_eye.ear,
                    raw_eye.left_ear,
                    raw_eye.right_ear,
                    reason,
                )
            current_closure = eye_timer.update(packet.source_time_s, current_eye)
            current_perclos = perclos_window.update(packet.source_time_s, current_eye.state)
            mouth_cfg = _section(config, "mouth")
            if mouth_cfg.get("enabled", False):
                current_mouth_observation = mouth_detector.observe(face)
            else:
                current_mouth_observation = MouthObservation(MouthState.UNKNOWN, None, "channel_disabled")
            current_mouth = mouth_tracker.update(packet.source_time_s, current_mouth_observation)
            try:
                current_pose = pose_estimator.estimate(face)
            except Exception as exc:
                current_pose = HeadPoseObservation(False, None, None, None, None, None, None, None, None, f"head_pose_error:{type(exc).__name__}")

            previous_head_distraction = current_head_distraction
            current_head_distraction = head_distraction_tracker.update(
                run_time_s,
                yaw_deg=current_pose.relative_yaw_deg,
                pitch_deg=current_pose.relative_pitch_deg,
                roll_deg=current_pose.relative_roll_deg,
                valid=(
                    bool(head_distraction_cfg.get("enabled", False))
                    and current_pose.valid
                    and current_pose.relative_yaw_deg is not None
                    and current_pose.relative_pitch_deg is not None
                ),
            )
            _log_head_distraction_transition(
                logger,
                previous_head_distraction,
                current_head_distraction,
                run_time_s,
            )

            if phone_detector is not None and (last_phone_time_s is None or packet.source_time_s - last_phone_time_s >= phone_period):
                current_phone = phone_detector.observe(packet.image)
                last_phone_time_s = packet.source_time_s
            if hand_detector is not None and (last_hand_time_s is None or packet.source_time_s - last_hand_time_s >= hand_period):
                try:
                    current_hands = hand_detector.observe(packet.image, task_timestamp_ms)
                except Exception as exc:
                    current_hands = HandObservation(False, (), f"hand_detector_error:{type(exc).__name__}")
                last_hand_time_s = packet.source_time_s

            phone_age_s = None if last_phone_time_s is None else max(0.0, packet.source_time_s - last_phone_time_s)
            hand_age_s = None if last_hand_time_s is None else max(0.0, packet.source_time_s - last_hand_time_s)
            current_interaction = interaction_estimator.observe(current_phone, current_hands)
            closure_fraction = None
            if current_eye.state is not EyeState.UNKNOWN:
                closure_fraction = 0.0 if current_closure.observed_duration_s is None else min(
                    1.0,
                    current_closure.observed_duration_s / float(eyes_cfg["critical_closure_seconds"]),
                )
            fusion = fusion_engine.evaluate(
                eye=current_eye,
                closure_fraction=closure_fraction,
                perclos=current_perclos,
                mouth=current_mouth,
                head_pose=current_pose,
                interaction=current_interaction,
                critical_event_id=current_closure.event_id,
                phone_age_s=phone_age_s,
                head_distraction_active=current_head_distraction.active,
                head_distraction_valid=current_head_distraction.valid,
                head_distraction_reasons=current_head_distraction.active_reasons,
            )

            snapshot = capture.snapshot()
            capture_health, capture_age_s = watchdog.health(snapshot)
            if capture_health != last_health_log:
                logger.write({
                    "type": "health_transition",
                    "run_time_s": _rounded(run_time_s, 4),
                    "capture_health": capture_health,
                    "capture_age_s": _rounded(capture_age_s, 4),
                    "capture_status": snapshot.status.value,
                    "reason": snapshot.error,
                    "behavior_state": "unknown_due_to_no_fresh_observation" if capture_health != "available" else "fresh_observation",
                })
                last_health_log = capture_health

            _log_channel_health(
                logger, channel_health, "face",
                "available" if face.valid else "unavailable",
                face.reason, run_time_s,
            )
            _log_channel_health(
                logger, channel_health, "eyes",
                "available" if current_eye.state is not EyeState.UNKNOWN else "unavailable",
                current_eye.reason, run_time_s,
            )
            _log_channel_health(
                logger, channel_health, "mouth",
                "disabled" if current_mouth_observation.reason == "channel_disabled" else (
                    "available" if current_mouth_observation.state is not MouthState.UNKNOWN else "unavailable"
                ),
                current_mouth_observation.reason, run_time_s,
            )
            pose_ready = current_head_distraction.valid
            _log_channel_health(
                logger, channel_health, "head_pose",
                "available" if pose_ready else ("disabled" if not pose_cfg.get("enabled", False) else "unavailable"),
                current_pose.reason, run_time_s,
            )
            _log_channel_health(
                logger, channel_health, "phone",
                "available" if current_phone.valid else ("disabled" if phone_detector is None and not _section(config, "phone").get("enabled", False) else "unavailable"),
                current_phone.reason, run_time_s,
            )
            _log_channel_health(
                logger, channel_health, "hands",
                "available" if current_hands.valid else ("disabled" if hand_detector is None and not _section(config, "hands").get("enabled", False) else "unavailable"),
                current_hands.reason, run_time_s,
            )
            _log_channel_health(
                logger, channel_health, "phone_interaction",
                "candidate_diagnostics_only_not_alarm_input"
                if interaction_estimator.driver_roi is not None and phone_detector is not None and hand_detector is not None
                else "unavailable",
                current_interaction.reason, run_time_s,
            )
            _log_channel_health(logger, channel_health, "audio", audio.status, audio.error, run_time_s)

            previous_drowsiness = current_drowsiness_alert
            previous_distraction = current_distraction_alert
            current_drowsiness_alert = drowsiness_machine.update(fusion.drowsiness, run_time_s)
            active_head_reasons = set(fusion.distraction.reasons) & _HEAD_DISTRACTION_REASONS
            phone_presence_candidate = "phone_presence" in fusion.distraction.reasons
            distraction_entry_override = (
                0.0
                if active_head_reasons
                else phone_presence_entry_s if phone_presence_candidate else None
            )
            # The head tracker has already required valid forward recovery for
            # its configured duration; don't stack the alert machine's timer
            # on top for a head-only episode.
            head_recovery_already_persisted = (
                previous_distraction.level is not AlertLevel.NONE
                and bool(previous_distraction.reasons)
                and bool(set(previous_distraction.reasons) & _HEAD_DISTRACTION_REASONS)
                and not current_head_distraction.active
            )
            phone_presence_resolved = (
                "phone_presence" in previous_distraction.reasons
                and not (set(previous_distraction.reasons) & _HEAD_DISTRACTION_REASONS)
                and not phone_presence_candidate
            )
            recovery_override = (
                0.0
                if head_recovery_already_persisted
                else phone_presence_recovery_s if phone_presence_resolved else None
            )
            current_distraction_alert = distraction_machine.update(
                fusion.distraction,
                run_time_s,
                entry_persistence_override_s=distraction_entry_override,
                recovery_persistence_override_s=recovery_override,
            )
            _log_alert_transition(logger, previous_drowsiness, current_drowsiness_alert, run_time_s)
            _log_alert_transition(logger, previous_distraction, current_distraction_alert, run_time_s)
            leading_alert = max(
                (current_drowsiness_alert, current_distraction_alert),
                key=lambda alert: alert.level,
            )
            audio.set_alert(leading_alert.level.name.lower(), leading_alert.episode_id)
            audio_state = audio.playback_state.value
            if audio_state != last_audio_state:
                logger.write({
                    "type": "audio_playback_transition",
                    "run_time_s": _rounded(run_time_s, 4),
                    "desired_level": leading_alert.level.name.lower(),
                    "playback_state": audio_state,
                    "episode_id": leading_alert.episode_id,
                    "audio_status": audio.status,
                })
                last_audio_state = audio_state
            for alert in (current_drowsiness_alert, current_distraction_alert):
                if alert.notify:
                    logger.write({
                        "type": "notification_request",
                        "run_time_s": _rounded(run_time_s, 4),
                        "channel": alert.channel,
                        "level": alert.level.name.lower(),
                        "episode_id": alert.episode_id,
                        "audio_active": audio.active,
                        "audio_playback_state": audio.playback_state.value,
                        "audio_status": audio.status,
                    })

            processing_ms = (time.perf_counter() - processing_start) * 1000.0
            _log_observation(
                logger,
                packet=packet,
                last_sequence=last_sequence,
                run_time_s=run_time_s,
                processing_ms=processing_ms,
                capture_health=capture_health,
                capture_age_s=capture_age_s,
                eye=current_eye,
                eye_threshold_mode=eye_threshold_mode,
                eye_calibration=current_eye_calibration,
                closure=current_closure,
                perclos=current_perclos,
                mouth_observation=current_mouth_observation,
                mouth=current_mouth,
                pose=current_pose,
                phone=current_phone,
                phone_age_s=phone_age_s,
                hands=current_hands,
                hand_age_s=hand_age_s,
                interaction=current_interaction,
                head_distraction=current_head_distraction,
                face_tracking_available=face.valid,
                face_detection_confidence_gate=face_detection_confidence_gate,
                audio_status=audio.status,
                audio_playback_state=audio.playback_state,
                fusion=fusion,
                drowsiness_alert=current_drowsiness_alert,
                distraction_alert=current_distraction_alert,
            )

            resource_wall_now = time.monotonic()
            if resource_wall_now >= next_resource_at:
                process_time_now = time.process_time()
                elapsed = max(1e-9, resource_wall_now - last_resource_wall)
                rss_mb = None
                if resource_process is not None:
                    try:
                        rss_mb = resource_process.memory_info().rss / (1024.0 * 1024.0)
                    except Exception:
                        rss_mb = None
                logger.write({
                    "type": "resource_sample",
                    "run_time_s": _rounded(run_time_s, 4),
                    "process_cpu_percent": _rounded((process_time_now - last_cpu_time) / elapsed * 100.0, 2),
                    "rss_mb": _rounded(rss_mb, 2),
                })
                last_cpu_time = process_time_now
                last_resource_wall = resource_wall_now
                next_resource_at = resource_wall_now + 1.0

            if audio.error and audio.error != last_audio_error_logged:
                logger.write({"type": "audio_error", "run_time_s": _rounded(run_time_s, 4), "error": audio.error})
                last_audio_error_logged = audio.error
            if preview:
                if cv2 is None:
                    import cv2 as _cv2
                    cv2 = _cv2
                cv2.imshow("Driver Monitoring Research Prototype", _overlay(
                    cv2,
                    packet.image,
                    capture_health=capture_health,
                    eye=current_eye,
                    closure=current_closure,
                    perclos=current_perclos,
                    mouth=current_mouth,
                    pose=current_pose,
                    phone=current_phone,
                    hands=current_hands,
                    interaction=current_interaction,
                    head_distraction=current_head_distraction,
                    head_turn_threshold_s=head_distraction_tracker.head_turn_duration_s,
                    downward_head_threshold_s=head_distraction_tracker.downward_duration_s,
                    face_tracking_available=face.valid,
                    face_detection_confidence_gate=face_detection_confidence_gate,
                    audio_status=audio.status,
                    audio_playback_state=audio.playback_state,
                    drowsiness_alert=current_drowsiness_alert,
                    distraction_alert=current_distraction_alert,
                    eye_threshold_mode=eye_threshold_mode,
                    eye_calibration=current_eye_calibration,
                ))
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key in {ord("c"), ord("C")}:
                    current_eye_calibration = _start_eye_calibration(
                        eye_calibrator, logger, run_time_s, eye_threshold_mode
                    )
                if key in {ord("x"), ord("X")} and eye_calibrator.state is EyeCalibrationState.COLLECTING:
                    current_eye_calibration = _cancel_eye_calibration(
                        eye_calibrator, logger, run_time_s, eye_threshold_mode
                    )
                if key in {ord("n"), ord("N")} and pose_cfg.get("enabled", False):
                    pose_estimator.reset_neutral()
                    previous_head_distraction = current_head_distraction
                    head_distraction_tracker.reset(run_time_s)
                    current_head_distraction = head_distraction_tracker.update(
                        run_time_s + 1e-9,
                        yaw_deg=None,
                        pitch_deg=None,
                        roll_deg=None,
                        valid=False,
                    )
                    _log_head_distraction_transition(
                        logger,
                        previous_head_distraction,
                        current_head_distraction,
                        run_time_s,
                    )
                    logger.write({
                        "type": "head_pose_baseline_reset",
                        "run_time_s": _rounded(run_time_s, 4),
                        "reason": "operator_requested_recalibration",
                    })

            last_sequence = packet.sequence
            last_packet = packet
            packet = None

        return 0
    finally:
        # Stop the warning stream first when the operator exits or capture
        # closes, then release camera and detector resources.
        audio.close()
        capture.stop()
        face_detector.close()
        if hand_detector is not None:
            hand_detector.close()
        if phone_detector is not None:
            phone_detector.close()
        if logger is not None:
            if eye_calibrator.state is not EyeCalibrationState.IDLE:
                final_calibration = eye_calibrator.snapshot(
                    (time.monotonic_ns() - start_ns) / 1_000_000_000.0
                )
                logger.write({
                    "type": "eye_calibration_summary",
                    "threshold_mode": eye_threshold_mode,
                    "state": final_calibration.state.value,
                    "elapsed_s": _rounded(final_calibration.elapsed_s, 3),
                    "sample_count": final_calibration.sample_count,
                    "open_eye_ear": _rounded(final_calibration.open_ear),
                    "closed_threshold": _rounded(final_calibration.closed_threshold),
                    "relative_mad": _rounded(final_calibration.relative_mad),
                    "reason": final_calibration.reason,
                })
            logger.close()
            if logger.dropped_records:
                print(f"Metadata log dropped {logger.dropped_records} records", file=sys.stderr)
            if logger.error:
                print(logger.error, file=sys.stderr)
        if preview:
            if cv2 is None:
                try:
                    import cv2 as _cv2
                    cv2 = _cv2
                except ImportError:
                    cv2 = None
            if cv2 is not None:
                cv2.destroyAllWindows()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local multimodal driver-monitoring research prototype")
    parser.add_argument("--config", default="configs/full.yaml", help="YAML configuration path")
    parser.add_argument("--source", help="camera index (for example 0) or local video file path")
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="report required and configured optional model files and verify pinned SHA-256 values",
    )
    parser.add_argument(
        "--download-models",
        action="store_true",
        help="download missing required model files for the selected configuration",
    )
    parser.add_argument(
        "--all",
        dest="all_models",
        action="store_true",
        help="with --download-models, include all supported optional model bundles",
    )
    parser.add_argument("--run-dir", help="new output directory; existing directories are never overwritten")
    parser.add_argument("--seconds", type=float, help="stop after this many seconds")
    parser.add_argument("--headless", action="store_true", help="disable the preview window")
    parser.add_argument("--fusion-method", choices=("rules", "weighted", "calibrated"), help="override the configured fusion strategy")
    parser.add_argument(
        "--eye-threshold-mode",
        choices=("fixed", "personalized", "personalized-quality"),
        help="choose fixed EAR, calibrated personal EAR, or calibrated EAR with bilateral quality gating",
    )
    parser.add_argument(
        "--calibrate-eyes-at-start",
        action="store_true",
        help="start the configured 20–30 second open-eye calibration immediately (useful in headless runs)",
    )
    return parser


def _warn_about_optional_models(checks: tuple[ModelCheck, ...], root: Path) -> None:
    for check in checks:
        requirement = check.requirement
        if requirement.required:
            if check.status in {"missing", "invalid", "not_configured"}:
                print(f"dms: warning: {check_line(check, root)}", file=sys.stderr)
            elif check.status == "unverified":
                print(
                    f"dms: warning: required custom model has no project-pinned SHA-256: "
                    f"{requirement.path}. Its model format will be checked by the detector at load time.",
                    file=sys.stderr,
                )
            continue
        if check.status == "missing":
            if requirement.asset is not None:
                next_step = "Run `drive --download-models --all` to install it."
            else:
                next_step = "Install a trusted compatible file manually; no project download is registered."
            print(
                f"dms: warning: optional {requirement.label} is missing at {requirement.path}; "
                f"that detector will be unavailable. {next_step}",
                file=sys.stderr,
            )
        elif check.status == "invalid":
            print(
                f"dms: warning: optional model rejected: {check_line(check, root)}. "
                "That detector will be unavailable; the existing file was left untouched. "
                "Move or remove it, then run `drive --download-models --all` to fetch the pinned bundle.",
                file=sys.stderr,
            )
        elif check.status == "unverified":
            print(
                f"dms: warning: custom model has no project-pinned SHA-256: "
                f"{requirement.path}. Its model format will be checked by the detector at load time.",
                file=sys.stderr,
            )


def _model_command(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = _load_config(config_path)
    root = config_path.parent.parent
    if args.fusion_method is not None:
        config = dict(config)
        fusion = dict(_section(config, "fusion"))
        fusion["method"] = args.fusion_method
        config["fusion"] = fusion

    requirements = configured_model_requirements(config, root)
    if args.check_models:
        checks = inspect_configured_models(config, root)
        print(f"Model files for {config_path}:")
        for check in checks:
            print(f"  {check_line(check, root)}")
        if any(item.status == "missing" and not item.requirement.required for item in checks):
            print("Optional model bundles can be installed with `drive --download-models --all`.")
        if any(item.status == "unverified" for item in checks):
            print("Custom model files have no project-pinned digest; review their source and compatibility.")
        return 1 if required_model_failure(checks) else 0

    if args.all_models:
        selected = registered_model_requirements(root, requirements)
        print("Downloading missing supported model bundles (required and optional):")
    else:
        selected = tuple(item for item in requirements if item.required and item.asset is not None)
        print("Downloading missing required model files for the selected configuration:")

    if not selected:
        print("No automatically downloadable model files are required by this configuration.")
    results = download_missing_models(selected, root)
    failed = False
    for result in results:
        if result.status == "downloaded":
            print(f"  Downloaded and SHA-256 verified: {result.asset.label} -> {result.path}")
        elif result.status == "already_verified":
            print(f"  Already present and SHA-256 verified: {result.path}")
        else:
            failed = True
            print(f"  ERROR {result.asset.label}: {result.message}", file=sys.stderr)

    checks = inspect_configured_models(config, root)
    print("\nModel status after download:")
    for check in checks:
        print(f"  {check_line(check, root)}")
    if any(item.status == "missing" and not item.requirement.required for item in checks):
        print("Optional model bundles not included in this download can be installed with `drive --download-models --all`.")
    if any(item.status == "unverified" for item in checks):
        print("Custom model files have no project-pinned digest; review their source and compatibility.")
    return 1 if failed or required_model_failure(checks) else 0


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.check_models and args.download_models:
        parser.error("choose either --check-models or --download-models")
    if args.all_models and not args.download_models:
        parser.error("--all is only valid with --download-models")
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("--seconds must be a positive finite number")
    try:
        if args.check_models or args.download_models:
            return _model_command(args)
        return _run(args)
    except (FileNotFoundError, ImportError, ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"dms: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
