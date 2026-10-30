# Safe Staged Pilot Protocol

This is an engineering protocol template for supervised prototype checks. It
does not replace institutional ethics review, informed consent, local law, or a
vehicle manufacturer's instructions.

## Scope

Use a stationary, parked vehicle or a lab setup. Keep the vehicle off public
roads, in park, with the parking brake applied, and the engine off where
practical. Have a supervisor present. The pilot evaluates posed events and
camera behavior; it does not validate real-world drowsiness detection.

Do not induce sleep deprivation or ask participants to use the system while
driving. Stop immediately if anyone feels unwell, distressed, or uncomfortable.
Participation is voluntary and may be stopped without explanation.

## Before a session

1. Obtain required ethics approval and written informed consent before any
   participant recording or analysis.
2. Explain what is processed and logged. The app stores derived EAR and other
   configured features in local JSONL logs; these may still be personal data.
   Default video and audio recording are disabled.
3. Confirm the camera view, device label, requested and actual resolution, and
   that no unrelated people or sensitive material are in frame.
4. Confirm the output directory is access-restricted and has enough disk space.
5. Verify that alerts are not being relied on for any real safety decision.
6. If personalized EAR calibration is part of the protocol, tell the
   participant to sit naturally, face the camera, and keep their eyes open for
   the full 20–30 second calibration. Save only the data approved by the study.

## Staged events

Use a predefined, randomized or counterbalanced cue sheet and timestamp each
cue. Examples include a brief eye closure, a yawn-like mouth opening, looking
away, or picking up a phone. Define each event duration and the neutral
interval before data collection. Do not ask participants to sustain an
uncomfortable pose. Record non-event intervals so false alarms can be
measured.

These events are intentionally posed. Describe the results as prototype
validation on staged behavior and camera conditions. They cannot establish
performance during natural fatigue, actual phone use, or road driving.

## After a session

- Confirm that the run manifest and feature log contain the expected session
  metadata and no raw frames or audio.
- Record the consent/approval reference in the study's access-controlled
  records, not in public run logs.
- Preserve the original logs and hashes; create separate derived analysis
  files.
- Follow the approved retention and deletion schedule.
- Report missing observations, unknown time, camera failures, and protocol
  deviations alongside event metrics.
