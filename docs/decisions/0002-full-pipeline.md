# Decision 0002: modular multimodal research pipeline

**Date:** 30 October 2026

**Status:** Source implemented; physical device and empirical validation pending.

## Decision

Keep one local face-landmark inference pass for eyes, mouth geometry, and
calibrated head pose. Isolate optional phone and hand inference behind separate
adapters. Fuse evidence into independent drowsiness and distraction decisions,
then use separate alert state machines. Record only metadata and derived
features by default.

## Behavior and evidence boundaries

- Missing, stale, or poor-quality observations remain unknown and are not
  treated as a negative example.
- Eye closure and PERCLOS use source timestamps and explicit continuity gaps.
- Sustained mouth opening is an opening candidate, not a yawn classification.
- Face-transform or generic solvePnP geometry estimates head direction after
  a neutral baseline; thresholds still need movement validation. Pose is not
  gaze.
- Phone presence is not phone-use classification. The full single-driver
  profile warns on a detected phone in the configured driver ROI, while
  phone/hand proximity remains separately logged as interaction evidence.
- Rule-based fusion is the default. Weighted fusion preserves a fixed score
  scale when channels are absent. Calibrated fusion returns unknown unless
  every model feature is present and fresh.
- A critical eye-closure path can bypass other evidence. Other alerts use
  entry/recovery persistence, priority, episode IDs, and cooldown.
- The configs enable one synthesized continuous piezo-style square-wave tone
  for active warnings; a single PortAudio stream serves all warning levels.
- The fixed EAR threshold remains the comparison baseline. Per-person EAR
  calibration is an explicit opt-in ablation that is manually started, kept in
  memory, quality checked, and reported in run metadata. Personalized modes
  remain unknown until enrollment succeeds; they are not validated defaults.

## Pending work outside the source tree

The repository does not include camera calibration, a saved neutral
reference, a narrowed driver ROI, annotated data, or a fitted fusion model.
Physical buzzer playback must be confirmed on a host with a configured audio
output device. No accuracy, false-alarm, real-time throughput, physical
latency, or safety claim follows from this source implementation.
