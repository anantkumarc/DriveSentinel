# Decision 0001: eye-first local milestone

**Date:** 30 October 2026

**Status:** Implemented as the minimal configuration; startup/model smoke checking is recorded in [environment notes](../environment.md). Camera and empirical validation remain pending.

## Decision

Start with OpenCV capture, MediaPipe Face Landmarker, bilateral EAR
observations, causal closure timing, a muted diagnostic preview, and
metadata-only JSONL logging. Keep this eye-only baseline available through
`configs/minimal.yaml` while implementing the wider research pipeline.

## Reasons

- This matches the research handover's first runnable milestone and makes the
  eye-only baseline available before multimodal fusion.
- The handover identifies the eye-only temporal baseline as the comparison
  point for later fusion.
- Keep the configuration usable without a dedicated accelerator or external
  camera; actual camera support depends on the host and its permissions.

## Provisional behavior

EAR `0.20`, minimum eye width `12 px`, maximum continuity gap `0.25 s`, and
critical closure candidate `1.5 s` are unvalidated examples. They are surfaced
as configuration and labeled in the UI; they are not safety limits.

## Pending evidence

Python 3.11 dependency installation and face-model loading have been checked
locally. Live camera behavior, accuracy, false alarms/hour, latency, resource
usage, and participant protocol remain unverified. The initial implementation
uses the documented MediaPipe Tasks API; broader host compatibility must be
confirmed.
