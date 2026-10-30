"""Optional ONNX phone-presence adapter for YOLOv8 or YOLOX raw outputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    class_id: int


@dataclass(frozen=True, slots=True)
class PhoneDetection:
    valid: bool
    boxes: tuple[Box, ...]
    reason: str | None


class OnnxPhoneDetector:
    """Run a supplied ONNX model; presence never implies phone use."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        output_format: str = "yolov8",
        image_size: int = 640,
        phone_class_id: int = 67,
        score_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        max_detections: int = 20,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"phone ONNX model not found: {path}")
        if output_format not in {"yolov8", "yolox"}:
            raise ValueError("output_format must be yolov8 or yolox")
        if image_size < 32 or not 0 <= score_threshold <= 1 or not 0 <= nms_threshold <= 1:
            raise ValueError("invalid phone detector configuration")
        import onnxruntime as ort

        self.output_format = output_format
        self.image_size = image_size
        self.phone_class_id = phone_class_id
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.max_detections = max_detections
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("phone ONNX model must have exactly one image input")
        self._input_name = inputs[0].name

    def close(self) -> None:
        self._session = None

    def observe(self, bgr_image: Any) -> PhoneDetection:
        import cv2
        import numpy as np

        try:
            height, width = bgr_image.shape[:2]
            if width <= 0 or height <= 0:
                return PhoneDetection(False, (), "invalid_frame_dimensions")
            scale = min(self.image_size / width, self.image_size / height)
            resized_w = max(1, int(round(width * scale)))
            resized_h = max(1, int(round(height * scale)))
            resized = cv2.resize(bgr_image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
            pad_x = (self.image_size - resized_w) // 2
            pad_y = (self.image_size - resized_h) // 2
            canvas = np.full((self.image_size, self.image_size, 3), 114, dtype=np.uint8)
            canvas[pad_y : pad_y + resized_h, pad_x : pad_x + resized_w] = resized
            rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
            tensor = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None, ...]
            outputs = self._session.run(None, {self._input_name: tensor})
            if not outputs:
                return PhoneDetection(False, (), "onnx_model_returned_no_outputs")
            rows = self._rows(outputs[0])
            if rows is None:
                return PhoneDetection(False, (), "unsupported_onnx_output_shape")

            boxes: list[list[int]] = []
            scores: list[float] = []
            for row in rows:
                expected = self.phone_class_id + (5 if self.output_format == "yolox" else 4)
                if len(row) <= expected:
                    continue
                if self.output_format == "yolov8":
                    score = float(row[4 + self.phone_class_id])
                else:
                    score = float(row[4]) * float(row[5 + self.phone_class_id])
                if not math.isfinite(score) or score < self.score_threshold:
                    continue
                center_x, center_y, box_w, box_h = (float(v) for v in row[:4])
                x1 = (center_x - box_w / 2.0 - pad_x) / scale
                y1 = (center_y - box_h / 2.0 - pad_y) / scale
                x2 = (center_x + box_w / 2.0 - pad_x) / scale
                y2 = (center_y + box_h / 2.0 - pad_y) / scale
                x1 = max(0, min(width - 1, int(round(x1))))
                y1 = max(0, min(height - 1, int(round(y1))))
                x2 = max(0, min(width - 1, int(round(x2))))
                y2 = max(0, min(height - 1, int(round(y2))))
                if x2 <= x1 or y2 <= y1:
                    continue
                boxes.append([x1, y1, x2 - x1, y2 - y1])
                scores.append(score)

            indices = cv2.dnn.NMSBoxes(boxes, scores, self.score_threshold, self.nms_threshold)
            kept: list[Box] = []
            for index in list(indices)[: self.max_detections]:
                i = int(index[0] if hasattr(index, "__len__") else index)
                x, y, box_w, box_h = boxes[i]
                kept.append(
                    Box(
                        x / width,
                        y / height,
                        (x + box_w) / width,
                        (y + box_h) / height,
                        float(scores[i]),
                        self.phone_class_id,
                    )
                )
            return PhoneDetection(True, tuple(kept), None)
        except Exception as exc:
            return PhoneDetection(False, (), f"phone_detector_error:{type(exc).__name__}:{exc}")

    def _rows(self, output: Any):
        import numpy as np

        array = np.asarray(output)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != 2:
            return None
        if self.output_format == "yolov8" and array.shape[0] < array.shape[1] and array.shape[0] <= 256:
            array = array.T
        if array.shape[1] < self.phone_class_id + (6 if self.output_format == "yolox" else 5):
            return None
        return array


class MediaPipePhoneDetector:
    """COCO cell-phone detection using a MediaPipe Object Detector TFLite model."""

    _PHONE_LABELS = {"cell phone", "mobile phone", "cellphone", "phone"}
    _PHONE_LABEL_INDEX = 76  # Google MediaPipe COCO label map.

    def __init__(self, model_path: str | Path, *, score_threshold: float = 0.30) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"phone TFLite model not found: {path}")
        if not 0 <= score_threshold <= 1:
            raise ValueError("phone score threshold must be between zero and one")
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.ObjectDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            max_results=50,
            score_threshold=score_threshold,
        )
        self._task = mp.tasks.vision.ObjectDetector.create_from_options(options)

    def close(self) -> None:
        self._task.close()

    def observe(self, bgr_image: Any) -> PhoneDetection:
        import cv2
        import numpy as np

        height, width = bgr_image.shape[:2]
        if width <= 0 or height <= 0:
            return PhoneDetection(False, (), "invalid_frame_dimensions")
        try:
            rgb = np.ascontiguousarray(cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB))
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            result = self._task.detect(image)
            boxes: list[Box] = []
            for detection in result.detections:
                categories = getattr(detection, "categories", ())
                if not categories:
                    continue
                category = max(categories, key=lambda item: float(item.score or 0.0))
                name = str(getattr(category, "category_name", "") or "").strip().casefold()
                index = getattr(category, "index", None)
                if name not in self._PHONE_LABELS and index != self._PHONE_LABEL_INDEX:
                    continue
                region = detection.bounding_box
                left = max(0, min(width, int(region.origin_x)))
                top = max(0, min(height, int(region.origin_y)))
                right = max(0, min(width, int(region.origin_x + region.width)))
                bottom = max(0, min(height, int(region.origin_y + region.height)))
                if right <= left or bottom <= top:
                    continue
                boxes.append(Box(
                    left / width,
                    top / height,
                    right / width,
                    bottom / height,
                    float(category.score),
                    67,
                ))
            return PhoneDetection(True, tuple(boxes), None)
        except Exception as exc:
            return PhoneDetection(False, (), f"phone_detector_error:{type(exc).__name__}:{exc}")
