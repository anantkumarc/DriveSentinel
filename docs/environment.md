# Environment and verification notes

This document records the scope of local checks without publishing details
about the developer's computer or local file paths.

## Supported environment

- Project metadata supports Python 3.11 through 3.14.
- Python 3.11 is selected in `.python-version`.
- The declared dependencies and lock file are managed through
  `pyproject.toml` and `uv.lock`.
- OpenCV is constrained below major version 5; MediaPipe is pinned to 1.2.0.
- A camera recognized by OpenCV and an available audio output device are
  needed to exercise live video and audible warnings.

The checks recorded here were performed in a macOS development environment
with Python 3.11. They do not establish compatibility on every macOS version
or on Linux and Windows.

## Verification snapshot

| Check | Result |
|---|---|
| Dependency resolution and installation | Completed in the local Python 3.11 environment. |
| Automated unit tests | 30 tests passed with `python -m unittest discover -s tests`. |
| MediaPipe face-model smoke check | Passed with the local Face Landmarker model. |
| Short synthetic-video application run | Completed and produced derived feature logs. |
| Live-camera operation | Not verified; camera permission and device behavior remain setup-specific. |
| Physical buzzer playback | Not verified; the local runtime did not expose an audio output device. |
| Detection accuracy, false-alarm rate, and real-time performance | Not measured. |

These checks cover startup and a synthetic input path only. They do not
establish driver-monitoring accuracy, warning latency, or safety.

## Models and local configuration

The face model and optional hand and phone models are excluded from Git.
Their acquisition sources and SHA-256 values are recorded in
[models/README.md](../models/README.md). Per-camera calibration, neutral-pose
files, run directories, and calibrated fusion models are local artifacts and
should not be published.

For head-pose sign and timing checks, follow
[the head-distraction validation procedure](head-distraction-validation.md)
with the intended camera placement while parked. Do not perform movement
tests while driving.
