# Model assets

Model weights are not stored in Git. Download the files needed for your
configuration into this directory and verify their SHA-256 values before use.
The current full configuration uses the face, hand, and phone model files
listed below. The main face, eye, mouth, and head-pose processing path requires
the face model.

| Asset | Used by | Source |
|---|---|---|
| `face_landmarker.task` | Face, eye, mouth, and head-pose landmarks | [Face Landmarker model bundle](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task) |
| `hand_landmarker.task` | Hand landmarks and diagnostic phone/hand association | [Hand Landmarker model bundle](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task) |
| `efficientdet_lite0.tflite` | Phone-presence detection | [EfficientDet-Lite0 INT8 model](https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/latest/efficientdet_lite0.tflite) |
| `fusion_drowsiness.json`, `fusion_distraction.json` | Optional learned fusion | Generated from development data with `tools/fit_fusion.py`; not included or pre-trained. |

The download URLs use Google's `latest` alias and may change over time. The
hashes and retrieval date below identify the versions inspected for this
project. A downloaded file with a different hash may be a newer model; verify
its source and compatibility before using it.

SHA-256 for the model files downloaded on 30 October 2026:

```text
face_landmarker.task       64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff
hand_landmarker.task       fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1
efficientdet_lite0.tflite  0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb
```

The MediaPipe model cards identify the bundled face components as Apache 2.0:
[BlazeFace card](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Short%20Range%29.pdf),
[Face Mesh V2 card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf),
and [Blendshape V2 card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf).
The [MediaPipe Object Detector guide](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector#models)
and [Hand Landmarker guide](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker#models)
describe the other task models. The EfficientDet-Lite0 COCO label map uses
category 76 for `cell phone`; the adapter also matches the category name.

For a fresh checkout, create this directory if needed and download only the
models required by the selected configuration. Keep model files and any
calibrated fusion artifacts out of public commits unless their redistribution
terms and data provenance are clear.
