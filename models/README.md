# Model assets

Model weights are not stored in Git. The `drive` command includes a model
manager that checks configured files and downloads registered model bundles
only after verifying their pinned SHA-256 digest. Run these commands from any
directory while the project's virtual environment is active:

```sh
drive --check-models
drive --download-models
drive --check-models
```

With the default `configs/full.yaml`, the face model is required; the phone and
hand models are optional. `drive --download-models` fetches only required,
registered bundles. To install all three official bundles for the full
configuration, run:

```sh
drive --download-models --all
```

Select another configuration with `--config`, for example
`drive --config configs/minimal.yaml --download-models`. All files are placed
under this repository's `models/` directory, independent of the current
terminal directory. The manager does not overwrite an existing file. A digest
mismatch or interrupted transfer is reported and its partial file is removed.
If Google's upstream `latest` alias points to new bytes, the download is
rejected until a maintainer reviews the new model and updates the recorded
digest.

| Asset | Used by | Source |
|---|---|---|
| `face_landmarker.task` | Face, eye, mouth, and head-pose landmarks | [Face Landmarker model bundle](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task) |
| `hand_landmarker.task` | Hand landmarks and diagnostic phone/hand association | [Hand Landmarker model bundle](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task) |
| `efficientdet_lite0.tflite` | Phone-presence detection | [EfficientDet-Lite0 INT8 model](https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/latest/efficientdet_lite0.tflite) |
| `fusion_drowsiness.json`, `fusion_distraction.json` | Optional learned fusion | Generated from development data with `tools/fit_fusion.py`; not included or pre-trained. |

The download URLs use Google's `latest` alias and may change over time. The
hashes and retrieval date below identify the versions pinned by this project.
The manager verifies these exact values. A different hash may be a newer model,
but it is not accepted automatically; verify its source and compatibility
before updating the pin.

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

For manual installs or custom configurations, verify a file with
`shasum -a 256 <file>` on macOS or Linux, or
`Get-FileHash <file> -Algorithm SHA256` in PowerShell. The face model is
required by the main pipeline. Without a configured optional phone or hand
model, that detector is unavailable and startup continues with a warning.

Custom ONNX models and calibrated fusion JSON artifacts do not have an
approved project download URL and pinned digest, so the manager will not fetch
them. Calibrated fusion files are generated with `tools/fit_fusion.py` from
appropriate development data; they are not included or pre-trained. Keep model
files and fitted artifacts out of public commits unless redistribution terms
and data provenance are clear.
