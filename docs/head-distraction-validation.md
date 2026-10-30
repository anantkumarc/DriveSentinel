# Head distraction and audio validation

This procedure validates the camera's angle signs and the complete
pose-to-audio path. Run it while parked in a safe location. Do not perform
these movements while driving. The thresholds are provisional engineering
settings, not validated driver-safety limits.

## Before the session

1. Position the camera so the driver's face stays visible during forward,
   left, right, and downward poses. Use the same camera, image orientation, and
   resolution that will be used for the application.
2. Confirm `head_pose.enabled` and `head_distraction.enabled` in
   `configs/full.yaml`. Keep the head facing forward for the automatic neutral
   enrollment at startup.
3. Check speaker volume and make sure the operating system is not muting the
   app. The app reports `audio_status` separately from `audio_playback_state`;
   `audio_status: failed` means PortAudio could not find an output device.
4. Start a fresh run and note the printed run directory:

   ```sh
   .venv/bin/python -m dms.app --config configs/full.yaml --source 0
   ```

   Press `q` to stop. Each run writes `manifest.json` and
   `observations.jsonl` under `runs/`.

## Movement sequence

Hold each pose steadily and return fully forward between steps. Wait for the
neutral baseline before starting the sequence.

Before the head sequence, place a phone inside the configured driver ROI to
confirm the separate phone-presence warning. In the full profile, phone
presence itself raises HIGH even when hand detection misses the hand; the
preview shows `presence warning: ACTIVE`. Remove the phone before testing head
poses so that this independent warning does not mask the head-cue results.

| Step | Action | Expected result |
| --- | --- | --- |
| 1 | Look straight for 3 seconds | Direction is `FORWARD`; all three duration timers stay near zero; no sustained head alert. |
| 2 | Move hands with no phone in the driver ROI | No alarm. Hand/phone association may be logged as a diagnostic candidate, but is excluded from alarm evidence. |
| 3 | Turn left for 4 seconds, then return forward | No HIGH alert; the left candidate timer resets on the valid normal pose. |
| 4 | Turn left for 6 seconds | Left timer crosses the 5 second sustain period; distraction becomes HIGH and audio becomes `PLAYING_BUZZER`. |
| 5 | Return forward | The head cue and piezo stream stop on the first valid observation below the left recovery angle. |
| 6 | Turn right for 4 seconds, then return forward | No HIGH alert; the right candidate timer resets independently. |
| 7 | Turn right for 6 seconds | Right timer independently crosses the 5 second threshold and produces a HIGH alert. |
| 8 | Look downward for 2 seconds | No HIGH alert. |
| 9 | Look downward for 4 seconds | Downward timer crosses its separate 3 second threshold and produces a HIGH alert. |
| 10 | Raise the head to normal | Downward cue and piezo stream stop on the first valid observation below the recovery angle. Looking upward must not count as looking down. |
| 11 | Combine a sustained turn and downward look, then clear only one | The alarm remains active while either independent head violation remains active. |
| 12 | Briefly move the face out of view, then return forward | Tracking is marked unavailable; missing landmarks are not treated as forward-facing or as recovery. |

If a physical left turn is labeled as right (or vice versa), inspect measured
relative yaw and change `left_yaw_sign` / `right_yaw_sign` in the config. Those
signs apply to pose measured from the unmirrored inference image relative to
the enrolled neutral head frame; the preview itself does not mirror pose
measurements. If the downward pose has the opposite sign, change
`downward_pitch_sign`. Confirm these settings by repeating the full sequence.

Yaw is measured relative to the driver's enrolled neutral head frame. The full
profile starts a turn candidate at 15 degrees and keeps it through the 10 degree
recovery band; the 5 second hold is unchanged. This makes moderate turns visible
without treating small movements below the recovery band as sustained turns.

## Review the run log

The preview shows measured relative yaw, pitch, and roll, direction, all three
timers, current risk, and audio playback state. `observations.jsonl` records
those values with the monotonic `run_time_s`, per-direction active flags and
episode IDs, face availability, alert decisions, risk level, and audio state.
`head_distraction_transition` records event entry and recovery. Inspect the
configured detector gate and the explicitly unavailable per-frame score with:

```sh
rg '"type": "(head_distraction_transition|audio_playback_transition|channel_health_transition)"|"head_relative_yaw_deg"|"head_relative_pitch_deg"|"head_left_duration_s"|"head_right_duration_s"|"head_down_duration_s"|"face_detection_confidence"|"risk_level"' runs/<run-directory>/observations.jsonl
```

MediaPipe's Face Landmarker applies minimum detection, presence, and tracking
confidence thresholds but its Python result does not provide a per-frame
detection confidence score. The log therefore writes
`face_detection_confidence: null`, `face_detection_confidence_reported: false`,
and the configured gate separately. `face_tracking_available` and pose
validity are measured states, not confidence scores.

## Automated checks and limits

The deterministic unit suite uses supplied monotonic timestamps and a fake
PortAudio output stream. It checks independent left/right 5 second and
downward 3 second timers, hand-candidate exclusion, angle signs, smoothing,
hysteresis, interrupted tracking, no false recovery on missing landmarks,
fusion, alert severity, continuous one-stream playback, recovery, generated
tone frequency and clipping bounds, and stream cleanup. It does not
verify the physical camera's yaw/pitch signs, loudspeaker output, or real-time
performance. Record those only after running the movement sequence above.
