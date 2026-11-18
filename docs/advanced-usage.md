# Advanced usage and detection details

These details describe the current prototype and provisional defaults. See the [main README](../README.md) for installation.

## Run and configure

To use a local video file instead of a camera:

```sh
drive --source path/to/video.mp4 --headless
```

To select the eye-first configuration:

```sh
drive --config configs/minimal.yaml
```

Press `q` in the preview to stop a windowed run. Each successful run creates a new directory under `runs/` with `manifest.json` and `observations.jsonl`; local run outputs are ignored by Git. On macOS, grant camera access to the terminal or application that starts the program. Use `drive --help` to see the command-line options.

The application defaults to `configs/full.yaml`. `configs/minimal.yaml` provides an eye-first baseline. The `--config` argument selects a configuration, and `--source` overrides its camera source. The source can be a camera index, local video file, or HTTP/RTSP stream URL.

### Selected full-profile settings

Values below come from `configs/full.yaml`. They are configurable engineering starting points, not validated safety limits.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `camera.source` | `0` | OpenCV camera index; `--source` can use a local video path |
| `camera.width`, `camera.height` | `640`, `480` | Requested capture dimensions |
| `eyes.ear_closed_threshold` | `0.20` | Fixed EAR closure threshold |
| `eyes.critical_closure_seconds` | `1.5` | Sustained eye-closure duration for the critical path |
| `eyes.perclos_window_seconds` | `60.0` | PERCLOS rolling-window duration |
| `eyes.perclos_min_coverage` | `0.5` | Minimum observed-time coverage before PERCLOS is valid |
| `mouth.open_ratio_threshold` | `0.55` | Mouth-opening ratio for an open observation |
| `mouth.candidate_after_seconds` | `1.0` | Sustained opening time before a candidate is emitted |
| `head_distraction.left_yaw_threshold_deg`, `right_yaw_threshold_deg` | `15.0` | Left/right head-turn activation angles |
| `head_distraction.head_turn_threshold_seconds` | `3.0` | Sustained left/right turn duration |
| `head_distraction.downward_pitch_threshold_deg` | `18.0` | Downward-pose activation angle |
| `head_distraction.downward_head_threshold_seconds` | `3.0` | Sustained downward-pose duration |
| `phone.score_threshold` | `0.20` | Minimum phone-detection score |
| `alerts.buzzer_frequency_hz` | `2400.0` | Synthesized warning-tone frequency |
| `alerts.buzzer_sample_rate_hz` | `48000` | Audio sample rate |
| `alerts.buzzer_volume` | `0.9` | Waveform amplitude setting |

Recovery angles, smoothing, detector confidence gates, phone region, fusion strategy, alert persistence, and other values are also configurable in the YAML files.

## Detection behavior

| Cue | Implemented behavior in the full profile |
| --- | --- |
| Forward head position | No head-direction warning while relative yaw and pitch remain inside their configured activation ranges. A stable neutral pose is enrolled at startup unless a neutral reference is supplied. |
| Left or right turn | Independent yaw timers use a 3-second configured duration. Smoothing and lower recovery angles help reduce frame noise and clear the cue after return toward neutral. |
| Looking downward | A downward-pitch timer uses a 3-second configured duration. The configured pitch sign distinguishes looking down from looking up. |
| Eye closure | Bilateral eye geometry feeds a timestamp-based closure tracker. The critical eye-closure path uses 1.5 seconds; the rule configuration also uses closure and PERCLOS evidence. |
| PERCLOS | Reports the closed-eye share over a 60-second window only when at least half of that window has valid observed-time coverage. |
| Mouth opening | A 1-second sustained opening produces a yawn-like candidate. Talking can also meet this condition; the code does not classify yawns. |
| Phone | A detected phone in the configured `driver_roi` can produce a distraction warning after 1 second of persistent detections in the full profile. The default region covers the full image, so presence may include a passenger or a false detection; it is not phone-use classification. |
| Hand movement | Hand landmarks and phone/hand proximity remain diagnostic. Hand movement alone and the phone/hand association do not trigger the alarm. |
| Audio | One continuous synthesized, band-limited square-wave tone (2.4 kHz by default) plays while an alert is active. Only one output stream runs at a time; frame-to-frame alert updates do not reopen it. Playback stops when the active alert clears or the application closes. |

The minimal configuration is an eye-first baseline; it does not configure mouth, pose, phone, hand, or learned-fusion channels; laptop audio is enabled. The full configuration enables the broader pipeline described above. The phone/hand association flag is not marked validated by default and is excluded from alarm evidence.


## Privacy and run logs

The default configuration does not save raw video, audio recordings, facial landmarks, or phone boxes. Run logs contain derived measurements, event and alert states, configuration and asset metadata, and capture/processing metadata. Treat run directories as potentially sensitive, review the privacy settings before collecting data, and do not commit private recordings, run logs, calibration files, credentials, or downloaded model weights.

## Limitations and future work

- Lighting, camera quality and placement, head angle, glasses, occlusion, and detector quality can affect landmark and pose estimates.
- The neutral-pose baseline and direction signs should be checked with the intended camera placement.
- A phone detected in a broad region can belong to a passenger or be a false detection. Phone presence is not phone-use classification.
- Mouth opening can occur during speech and is not a yawn classifier.
- No real-driver accuracy, false-alarm rate, throughput, or physical warning-latency result has been established.
- This prototype is not certified vehicle-safety equipment and is not a replacement for attentive driving or qualified safety systems.

Possible future work includes guided camera and neutral-pose setup, evaluation across more camera placements and lighting conditions, documented performance measurements, and privacy-reviewed collection of labeled data. These are plans, not completed capabilities.


## Connect a phone using USB

DriveSentinel can use a phone when the operating system recognizes it as a webcam. A charging-only cable is not sufficient; use a USB data cable.

**iPhone and Mac:** Use an iPhone XR or later with iOS 16+, and macOS Ventura 13+. Sign in to the same Apple Account with two-factor authentication on both devices, enable Wi-Fi and Bluetooth, and turn on Continuity Camera under iPhone Settings → General → AirPlay & Continuity (or AirPlay & Handoff). Connect the iPhone to the Mac by USB, tap Trust if asked, and mount the phone in landscape orientation. See [Apple's Continuity Camera requirements](https://support.apple.com/en-us/102546).

**Android and Mac:** Connect the phone using a USB data cable. On the phone, open the USB connection notification and select **Webcam** if it is offered. This option depends on the phone and Android version; if your Vivo does not offer it, the USB connection alone will not create a webcam. Use the hotspot method above or a compatible phone webcam app and Mac companion software. See [Google's Pixel webcam instructions](https://support.google.com/pixelcamera/answer/14274129).

Once the Mac recognizes the phone as a webcam, activate the project environment and run:

```sh
drive usb
```

Enter `1` when prompted. If it opens the laptop camera or reports that the camera is unavailable, quit and try another number such as `0` or `2`; device numbers vary. You can also run `drive usb 1` directly. Allow camera access for the terminal in macOS System Settings → Privacy & Security → Camera. Physical phone and Mac combinations have not been validated by the project.


## Additional verification

With the environment active, run `python tools/smoke.py --config configs/full.yaml --require-model` to check imports and configured face-model loading. Follow the [head-distraction validation procedure](head-distraction-validation.md) for stationary camera and direction checks. See [environment notes](environment.md) for the limits of recorded checks.
