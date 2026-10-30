# Decision 0003: opt-in personalized EAR calibration

**Date:** 30 October 2026

**Status:** Implemented in source; automated unit suite passes; not empirically validated.

## Decision

Keep the configured fixed EAR threshold as the default eye-only baseline. Add
two explicit comparison modes: a per-person threshold derived from an
operator-started open-eye calibration, and the same threshold with a bilateral
EAR asymmetry quality gate.

## Runtime behavior

- Calibration runs for 20–30 seconds only after the operator presses `c` or
  supplies `--calibrate-eyes-at-start`.
- The operator is instructed to face the camera with eyes open. Pressing `x`
  cancels an active preview calibration; pressing `c` again restarts it.
- The calibrator keeps only in-memory samples and accepts bilateral EAR values
  within configured plausibility and symmetry bounds.
- Completion requires a minimum sample count and stable robust spread. A
  rejected or incomplete calibration cannot enable the personalized eye
  channel.
- Personalized modes emit unknown eye state before successful calibration.
- The normalized closed threshold is the median open-eye EAR multiplied by a
  configured ratio. These starting values are provisional and need held-out
  evaluation before research conclusions.
- Calibration summary values are logged once at completion/exit. No raw
  calibration sample series is persisted; in-memory samples are cleared at
  completion, rejection, or cancellation.

## Limitations

The implementation cannot verify that a participant actually kept their eyes
open throughout enrollment. The visual prompt and stability checks reduce
obvious failures but do not establish calibration validity. EAR asymmetry is a
simple quality gate, not a general image-quality or eye-visibility measure.
Calibration performance must be evaluated against the fixed baseline with
subject-disjoint data before enabling it by default.
