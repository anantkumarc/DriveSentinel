from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

from dms.model_assets import (
    MODEL_ASSETS,
    ModelAsset,
    ModelIntegrityError,
    ModelRequirement,
    configured_model_requirements,
    download_missing_models,
    inspect_configured_models,
    inspect_requirement,
    resolve_model_path,
    verify_managed_model,
)


class _FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        interrupt_after: int | None = None,
        content_length: int | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.interrupt_after = interrupt_after
        self.position = 0

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self.interrupt_after is not None and self.position >= self.interrupt_after:
            raise OSError("simulated interrupted download")
        end = len(self.payload) if size < 0 else min(len(self.payload), self.position + size)
        if self.interrupt_after is not None:
            end = min(end, self.interrupt_after)
        chunk = self.payload[self.position:end]
        self.position = end
        return chunk


class ModelAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.payload = b"small, mocked model bundle"
        self.asset = ModelAsset(
            key="test",
            label="Test model",
            relative_path="models/test.task",
            url="https://models.example.invalid/test.task",
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )
        self.requirement = ModelRequirement(
            key="test",
            label="Test model",
            path=self.root / self.asset.relative_path,
            required=True,
            asset=self.asset,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_and_existing_valid_assets_are_reported(self) -> None:
        missing = inspect_requirement(self.requirement)
        self.assertEqual(missing.status, "missing")

        assert self.requirement.path is not None
        self.requirement.path.parent.mkdir(parents=True)
        self.requirement.path.write_bytes(self.payload)
        present = inspect_requirement(self.requirement)
        self.assertEqual(present.status, "verified")
        self.assertEqual(present.actual_sha256, self.asset.sha256)

    def test_sha256_mismatch_is_reported_as_invalid(self) -> None:
        assert self.requirement.path is not None
        self.requirement.path.parent.mkdir(parents=True)
        self.requirement.path.write_bytes(b"corrupted")

        result = inspect_requirement(self.requirement)
        self.assertEqual(result.status, "invalid")
        self.assertNotEqual(result.actual_sha256, self.asset.sha256)

    def test_download_is_verified_before_it_appears_at_destination(self) -> None:
        response = _FakeResponse(self.payload, content_length=len(self.payload))
        with patch("dms.model_assets.urlopen", return_value=response) as open_url:
            results = download_missing_models((self.requirement,), self.root)

        self.assertEqual(results[0].status, "downloaded")
        self.assertEqual(open_url.call_count, 1)
        assert self.requirement.path is not None
        self.assertEqual(self.requirement.path.read_bytes(), self.payload)
        self.assertEqual(inspect_requirement(self.requirement).status, "verified")

    def test_download_rejects_wrong_checksum_and_removes_partial_file(self) -> None:
        response = _FakeResponse(b"unexpected model bytes", content_length=22)
        with patch("dms.model_assets.urlopen", return_value=response):
            result = download_missing_models((self.requirement,), self.root)[0]

        self.assertEqual(result.status, "failed")
        self.assertIn("SHA-256 mismatch", result.message)
        assert self.requirement.path is not None
        self.assertFalse(self.requirement.path.exists())
        self.assertEqual(list(self.requirement.path.parent.glob("*.partial")), [])

    def test_interrupted_download_leaves_no_model_or_partial_file(self) -> None:
        response = _FakeResponse(
            self.payload,
            interrupt_after=5,
            content_length=len(self.payload),
        )
        with patch("dms.model_assets.urlopen", return_value=response):
            result = download_missing_models((self.requirement,), self.root)[0]

        self.assertEqual(result.status, "failed")
        self.assertIn("Could not download", result.message)
        assert self.requirement.path is not None
        self.assertFalse(self.requirement.path.exists())
        self.assertEqual(list(self.requirement.path.parent.glob("*.partial")), [])

    def test_one_failed_download_does_not_prevent_later_assets(self) -> None:
        second_payload = b"second valid model bundle"
        second_asset = ModelAsset(
            key="test_second",
            label="Second test model",
            relative_path="models/second.task",
            url="https://models.example.invalid/second.task",
            sha256=hashlib.sha256(second_payload).hexdigest(),
        )
        second_requirement = ModelRequirement(
            key="test_second",
            label="Second test model",
            path=self.root / second_asset.relative_path,
            required=False,
            asset=second_asset,
        )
        responses = [
            _FakeResponse(b"bad bytes"),
            _FakeResponse(second_payload, content_length=len(second_payload)),
        ]
        with patch("dms.model_assets.urlopen", side_effect=responses):
            results = download_missing_models(
                (self.requirement, second_requirement),
                self.root,
            )

        self.assertEqual([result.status for result in results], ["failed", "downloaded"])
        assert second_requirement.path is not None
        self.assertEqual(second_requirement.path.read_bytes(), second_payload)

    def test_managed_model_loader_guard_rejects_a_changed_pinned_asset(self) -> None:
        face_asset = MODEL_ASSETS[0]
        face_path = self.root / face_asset.relative_path
        face_path.parent.mkdir(parents=True)
        face_path.write_bytes(b"not the pinned face bundle")

        with self.assertRaisesRegex(ModelIntegrityError, "SHA-256 mismatch"):
            verify_managed_model(face_path, self.root)

    def test_existing_valid_file_is_never_downloaded_or_overwritten(self) -> None:
        assert self.requirement.path is not None
        self.requirement.path.parent.mkdir(parents=True)
        self.requirement.path.write_bytes(self.payload)
        with patch("dms.model_assets.urlopen") as open_url:
            result = download_missing_models((self.requirement,), self.root)[0]

        self.assertEqual(result.status, "already_verified")
        open_url.assert_not_called()
        self.assertEqual(self.requirement.path.read_bytes(), self.payload)

    def test_existing_invalid_file_is_preserved_and_not_downloaded(self) -> None:
        assert self.requirement.path is not None
        self.requirement.path.parent.mkdir(parents=True)
        original = b"keep this existing file untouched"
        self.requirement.path.write_bytes(original)
        with patch("dms.model_assets.urlopen") as open_url:
            result = download_missing_models((self.requirement,), self.root)[0]

        self.assertEqual(result.status, "failed")
        self.assertIn("was not overwritten", result.message)
        open_url.assert_not_called()
        self.assertEqual(self.requirement.path.read_bytes(), original)

    def test_configuration_marks_face_required_and_phone_hand_optional(self) -> None:
        config = {
            "face": {"model": "models/face_landmarker.task"},
            "phone": {
                "enabled": True,
                "backend": "mediapipe",
                "model": "models/efficientdet_lite0.tflite",
            },
            "hands": {"enabled": True, "model": "models/hand_landmarker.task"},
        }
        requirements = configured_model_requirements(config, self.root)
        checks = inspect_configured_models(config, self.root)
        by_key = {item.key: item for item in requirements}
        check_by_key = {item.requirement.key: item for item in checks}

        self.assertTrue(by_key["face"].required)
        self.assertFalse(by_key["phone"].required)
        self.assertFalse(by_key["hands"].required)
        self.assertEqual(check_by_key["face"].status, "missing")
        self.assertEqual(check_by_key["phone"].status, "missing")
        self.assertEqual(check_by_key["hands"].status, "missing")

    def test_custom_onnx_asset_is_reported_without_an_unverified_download_source(self) -> None:
        config = {
            "face": {"model": "models/face_landmarker.task"},
            "phone": {"enabled": True, "backend": "onnx", "model": "models/custom.onnx"},
        }
        phone = next(
            item for item in configured_model_requirements(config, self.root)
            if item.key == "phone"
        )
        self.assertIsNone(phone.asset)
        self.assertIn("no project-approved", phone.note)

    def test_model_paths_are_relative_and_convert_to_native_posix_or_windows_paths(self) -> None:
        asset = MODEL_ASSETS[0]
        relative = PurePosixPath(asset.relative_path)
        self.assertEqual(relative.parts, ("models", "face_landmarker.task"))
        windows_path = PureWindowsPath("C:/checkout") / PureWindowsPath(asset.relative_path)
        self.assertEqual(windows_path, PureWindowsPath("C:/checkout/models/face_landmarker.task"))

        resolved = resolve_model_path(self.root, asset.relative_path)
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.parts[-2:], ("models", "face_landmarker.task"))


if __name__ == "__main__":
    unittest.main()
