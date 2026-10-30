"""Integrity-checked downloads for the project's official model bundles."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True, slots=True)
class ModelAsset:
    """A project-pinned, downloadable third-party model file."""

    key: str
    label: str
    relative_path: str
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelRequirement:
    """A model file referenced by a selected application configuration."""

    key: str
    label: str
    path: Path | None
    required: bool
    asset: ModelAsset | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ModelCheck:
    requirement: ModelRequirement
    status: str
    actual_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    asset: ModelAsset
    path: Path
    status: str
    message: str = ""


class ModelManagementError(RuntimeError):
    """Base exception for model inventory and download failures."""


class ModelIntegrityError(ModelManagementError):
    """A downloaded or installed model does not match its trusted digest."""


class ModelDownloadError(ModelManagementError):
    """A model could not be retrieved or installed safely."""


# These URLs are the model-bundle URLs linked by Google's official MediaPipe
# task guides. The hashes pin the exact bundles verified in models/README.md.
# If an upstream `latest` alias changes, the mismatch is intentional: review
# the new model and digest before updating this registry.
MODEL_ASSETS: tuple[ModelAsset, ...] = (
    ModelAsset(
        key="face",
        label="Face Landmarker",
        relative_path="models/face_landmarker.task",
        url=(
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task"
        ),
        sha256="64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
    ),
    ModelAsset(
        key="phone",
        label="EfficientDet-Lite0 phone detector",
        relative_path="models/efficientdet_lite0.tflite",
        url=(
            "https://storage.googleapis.com/mediapipe-models/object_detector/"
            "efficientdet_lite0/int8/latest/efficientdet_lite0.tflite"
        ),
        sha256="0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb",
    ),
    ModelAsset(
        key="hands",
        label="Hand Landmarker",
        relative_path="models/hand_landmarker.task",
        url=(
            "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/latest/hand_landmarker.task"
        ),
        sha256="fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1",
    ),
)

_CHUNK_SIZE = 1024 * 1024
_MAX_MODEL_BYTES = 256 * 1024 * 1024


def resolve_model_path(project_root: Path, configured_path: str | Path) -> Path:
    """Resolve absolute or project-relative model paths portably."""
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = Path(project_root).expanduser() / path
    return path.resolve()


def asset_path(project_root: Path, asset: ModelAsset) -> Path:
    return resolve_model_path(project_root, asset.relative_path)


def _asset_for_path(project_root: Path, path: Path | None) -> ModelAsset | None:
    if path is None:
        return None
    canonical = path.resolve()
    for asset in MODEL_ASSETS:
        if asset_path(project_root, asset) == canonical:
            return asset
    return None


def configured_model_requirements(
    config: Mapping[str, Any],
    project_root: Path,
) -> tuple[ModelRequirement, ...]:
    """Describe required and enabled optional models for one YAML profile."""
    root = Path(project_root).expanduser().resolve()
    requirements: list[ModelRequirement] = []

    face = _mapping(config.get("face"))
    face_path = resolve_model_path(root, face.get("model", "models/face_landmarker.task"))
    requirements.append(
        ModelRequirement(
            key="face",
            label="Face Landmarker (required for startup)",
            path=face_path,
            required=True,
            asset=_asset_for_path(root, face_path),
            note=(
                "The face model is required by the main processing pipeline."
                if _asset_for_path(root, face_path) is not None
                else "Custom model paths have no project-approved download source; install a compatible file manually."
            ),
        )
    )

    phone = _mapping(config.get("phone"))
    if phone.get("enabled", False):
        backend = str(phone.get("backend", "onnx")).casefold()
        default_phone_path = (
            "models/efficientdet_lite0.tflite"
            if backend == "mediapipe"
            else "models/phone_detector.onnx"
        )
        phone_path = resolve_model_path(root, phone.get("model", default_phone_path))
        requirements.append(
            ModelRequirement(
                key="phone",
                label="Phone detector (optional)",
                path=phone_path,
                required=False,
                asset=_asset_for_path(root, phone_path),
                note=(
                    "Phone detection is optional."
                    if backend == "mediapipe"
                    else "This ONNX model has no project-approved download URL or pinned checksum."
                ),
            )
        )

    hands = _mapping(config.get("hands"))
    if hands.get("enabled", False):
        hands_path = resolve_model_path(
            root,
            hands.get("model", "models/hand_landmarker.task"),
        )
        requirements.append(
            ModelRequirement(
                key="hands",
                label="Hand Landmarker (optional diagnostics)",
                path=hands_path,
                required=False,
                asset=_asset_for_path(root, hands_path),
                note="Hand landmarks are optional diagnostics and do not independently trigger alarms.",
            )
        )

    fusion = _mapping(config.get("fusion"))
    if str(fusion.get("method", "rules")).casefold() == "calibrated":
        calibrated = _mapping(fusion.get("calibrated"))
        for channel in ("drowsiness", "distraction"):
            value = calibrated.get(channel)
            path = resolve_model_path(root, value) if value else None
            requirements.append(
                ModelRequirement(
                    key=f"fusion_{channel}",
                    label=f"Calibrated {channel} fusion model (manual artifact)",
                    path=path,
                    required=True,
                    asset=None,
                    note="Generated from evaluated project data; there is no official download source or pinned hash.",
                )
            )

    return tuple(requirements)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_requirement(requirement: ModelRequirement) -> ModelCheck:
    path = requirement.path
    if path is None:
        return ModelCheck(requirement, "not_configured")
    if not path.is_file():
        return ModelCheck(requirement, "missing")

    actual = sha256_file(path)
    if requirement.asset is None:
        return ModelCheck(requirement, "unverified", actual)
    if actual != requirement.asset.sha256:
        return ModelCheck(requirement, "invalid", actual)
    return ModelCheck(requirement, "verified", actual)


def inspect_configured_models(
    config: Mapping[str, Any],
    project_root: Path,
) -> tuple[ModelCheck, ...]:
    return tuple(
        inspect_requirement(requirement)
        for requirement in configured_model_requirements(config, project_root)
    )


def verify_managed_model(path: Path, project_root: Path) -> str | None:
    """Verify a registered file before loading; return None for custom paths."""
    asset = _asset_for_path(Path(project_root), Path(path))
    if asset is None or not Path(path).is_file():
        return None
    actual = sha256_file(Path(path))
    if actual != asset.sha256:
        raise ModelIntegrityError(
            f"SHA-256 mismatch for {asset.label} at {path}: expected "
            f"{asset.sha256}, got {actual}. The existing file was left untouched. "
            "Remove or move it yourself, then run `drive --download-models` to fetch the pinned file."
        )
    return actual


def registered_model_requirements(
    project_root: Path,
    configured: Iterable[ModelRequirement] = (),
) -> tuple[ModelRequirement, ...]:
    """Return every built-in downloadable asset, preserving configured required flags."""
    configured_by_key = {item.key: item for item in configured if item.asset is not None}
    result: list[ModelRequirement] = []
    for asset in MODEL_ASSETS:
        prior = configured_by_key.get(asset.key)
        result.append(
            ModelRequirement(
                key=asset.key,
                label=(prior.label if prior is not None else asset.label),
                path=asset_path(project_root, asset),
                required=bool(prior and prior.required),
                asset=asset,
                note=prior.note if prior is not None else "Supported official model bundle.",
            )
        )
    return tuple(result)


def download_model(
    requirement: ModelRequirement,
    project_root: Path,
    timeout_s: float = 30.0,
) -> DownloadResult:
    """Download one registered asset to a temporary file, verify, then install without clobbering."""
    asset = requirement.asset
    target = requirement.path
    if asset is None or target is None:
        raise ModelDownloadError(
            f"{requirement.label} has no project-approved download URL and checksum; install it manually."
        )
    canonical_target = asset_path(project_root, asset)
    if target.resolve() != canonical_target:
        raise ModelDownloadError(
            f"Refusing to download {asset.label} to the noncanonical path {target}. "
            f"The verified destination is {canonical_target}."
        )
    target = canonical_target
    if target.exists():
        existing = inspect_requirement(requirement)
        if existing.status == "verified":
            return DownloadResult(asset, target, "already_verified")
        if existing.status == "invalid":
            raise ModelIntegrityError(
                f"Existing {asset.label} at {target} has SHA-256 {existing.actual_sha256}; "
                f"expected {asset.sha256}. It was not overwritten. Move or remove it manually, "
                "then retry the download."
            )
        raise ModelDownloadError(f"Refusing to overwrite the existing path: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    digest = hashlib.sha256()
    received = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".partial",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with urlopen(asset.url, timeout=timeout_s) as response:
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    raise ModelDownloadError(
                        f"Unexpected HTTP status {status_code} while downloading {asset.label}."
                    )
                content_length = _content_length(response)
                if content_length is not None and content_length > _MAX_MODEL_BYTES:
                    raise ModelDownloadError(
                        f"Refusing unexpected {content_length}-byte response for {asset.label}."
                    )
                while True:
                    block = response.read(_CHUNK_SIZE)
                    if not block:
                        break
                    received += len(block)
                    if received > _MAX_MODEL_BYTES:
                        raise ModelDownloadError(
                            f"Refusing a response larger than {_MAX_MODEL_BYTES} bytes for {asset.label}."
                        )
                    temporary.write(block)
                    digest.update(block)
            temporary.flush()
            os.fsync(temporary.fileno())

        if content_length is not None and received != content_length:
            raise ModelDownloadError(
                f"Incomplete download for {asset.label}: received {received} of {content_length} bytes."
            )
        actual = digest.hexdigest()
        if actual != asset.sha256:
            raise ModelIntegrityError(
                f"SHA-256 mismatch for downloaded {asset.label}: expected {asset.sha256}, got {actual}. "
                "The downloaded file was discarded. The upstream model may have changed."
            )

        try:
            # A same-directory hard link is atomic and fails if another file
            # appeared at the destination. It never replaces an existing file.
            os.link(temporary_path, target)
        except FileExistsError:
            current = inspect_requirement(requirement)
            if current.status == "verified":
                return DownloadResult(asset, target, "already_verified")
            raise ModelIntegrityError(
                f"A file appeared at {target} while downloading and does not match the pinned checksum. "
                "It was left untouched."
            )
        except OSError as exc:
            raise ModelDownloadError(
                f"Could not install {asset.label} atomically at {target}: {exc}. "
                "Check that the destination filesystem supports hard links and is writable."
            ) from exc
        return DownloadResult(asset, target, "downloaded")
    except ModelManagementError:
        raise
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ModelDownloadError(f"Could not download {asset.label} from {asset.url}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def download_missing_models(
    requirements: Iterable[ModelRequirement],
    project_root: Path,
) -> tuple[DownloadResult, ...]:
    """Download missing registered requirements; preserve every existing file."""
    results: list[DownloadResult] = []
    for requirement in requirements:
        if requirement.asset is None or requirement.path is None:
            continue
        status = inspect_requirement(requirement)
        if status.status == "verified":
            results.append(DownloadResult(requirement.asset, requirement.path, "already_verified"))
        elif status.status == "missing":
            try:
                results.append(download_model(requirement, project_root))
            except ModelManagementError as exc:
                results.append(
                    DownloadResult(requirement.asset, requirement.path, "failed", str(exc))
                )
        elif status.status == "invalid":
            results.append(
                DownloadResult(
                    requirement.asset,
                    requirement.path,
                    "failed",
                    f"Existing file has SHA-256 {status.actual_sha256}; expected "
                    f"{requirement.asset.sha256}. It was not overwritten. Move or remove it manually, "
                    "then retry the download.",
                )
            )
        else:
            results.append(
                DownloadResult(
                    requirement.asset,
                    requirement.path,
                    "failed",
                    f"Cannot safely manage existing file at {requirement.path}.",
                )
            )
    return tuple(results)


def required_model_failure(checks: Iterable[ModelCheck]) -> bool:
    """Whether a required model is unavailable or any pinned model is invalid."""
    return any(
        item.requirement.required and item.status in {"missing", "invalid", "not_configured"}
        for item in checks
    ) or any(item.status == "invalid" for item in checks)


def display_path(path: Path | None, project_root: Path) -> str:
    if path is None:
        return "not configured"
    try:
        return path.resolve().relative_to(Path(project_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def check_line(check: ModelCheck, project_root: Path) -> str:
    requirement = check.requirement
    role = "required" if requirement.required else "optional"
    path = display_path(requirement.path, project_root)
    if check.status == "verified":
        detail = f"SHA-256 verified: {check.actual_sha256}"
    elif check.status == "invalid":
        expected = requirement.asset.sha256 if requirement.asset is not None else "not pinned"
        detail = f"INVALID SHA-256 (expected {expected}, got {check.actual_sha256})"
    elif check.status == "unverified":
        detail = f"present; SHA-256 {check.actual_sha256} has no project pin"
    elif check.status == "not_configured":
        detail = "path is not configured"
    else:
        detail = "missing"
    if requirement.asset is None and check.status in {"missing", "not_configured"}:
        detail = f"{detail}; no verified automatic download source. {requirement.note}"
    return f"[{role}] {requirement.label}: {path} — {detail}"


def _content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise ModelDownloadError("The server returned an invalid Content-Length header.") from exc
    if length < 0:
        raise ModelDownloadError("The server returned a negative Content-Length header.")
    return length

