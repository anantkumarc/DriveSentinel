# DriveSentinel

Camera-based driver-monitoring **research prototype**. It reads a camera feed, measures eye closure, head direction and other cues, and sounds a warning on an ESP32 buzzer (over Bluetooth) or on the laptop speakers.

> Not a certified vehicle safety system. Thresholds are configurable starting values, not validated safety limits.

```text
Android phone (IP Webcam)  --Wi-Fi-->  Laptop (DriveSentinel)  --BLE-->  ESP32-C3 + buzzer
```

| Platform | Status |
|---|---|
| Windows x64 | Documented target (end-to-end install not yet tested) |
| macOS Apple Silicon | Clean installation and headless sample-video run tested |
| macOS Intel | Not supported (no MediaPipe wheel) |
| Linux | Untested |

Python must be **64-bit, version 3.11 to 3.14**. Internet is needed during setup. Windows ARM64 is not validated with the complete dependency set.

---

## Before running (after installation)

1. Install **IP Webcam** from the Google Play Store on the Android phone.
2. Turn on the phone **hotspot** and connect the laptop to it.
3. Open IP Webcam and tap **Start server**. Note the complete address it shows, including the port (for example `http://192.168.43.1:8080`). Use it as `PHONE_ADDRESS` below.
4. Power on the programmed ESP32 and enable **Bluetooth** on the laptop.

---

## Complete commands, first to last

First install and set up the project while the laptop has internet access. Then follow **Before running** above and use the run command, replacing `PHONE_ADDRESS` with the complete address shown by IP Webcam.

### Windows (Command Prompt)

```bat
:: 1. Install tools (Arduino IDE is optional if the ESP32 is already programmed)
winget install -e --id Python.Python.3.11 --architecture x64
winget install -e --id Git.Git
```

If you need to program the ESP32, install Arduino IDE separately with `winget install -e --id ArduinoSA.IDE.stable`.

Close and reopen Command Prompt. Then check Python and install the project:

```bat
py -3 --version

:: 2. Download and set up the project (wait for "Setup complete")
git clone https://github.com/anantkumarc/DriveSentinel.git
cd DriveSentinel
py -3 setup_drive.py
```

After setup completes, start the phone server and power the ESP32 as described above. From the project folder, run:

```bat
.venv\Scripts\drive.exe wifi PHONE_ADDRESS --ble
```

### macOS (Terminal, Apple Silicon)

```bash
# 1. Install Homebrew (skip if "brew --version" already works)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

```

Follow the Homebrew installer’s displayed shell setup instructions before continuing. Then install the tools and project:

```bash
brew install python@3.11 git

# Download and set up the project (wait for "Setup complete")
git clone https://github.com/anantkumarc/DriveSentinel.git
cd DriveSentinel
python3.11 setup_drive.py
```

After setup completes, start the phone server and power the ESP32 as described above. From the project folder, run:

```bash
source .venv/bin/activate
drive wifi PHONE_ADDRESS --ble
```

### Linux (Ubuntu/Debian, untested)

```bash
# 1. Install tools
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip portaudio19-dev

# 2. Download and set up the project (wait for "Setup complete")
git clone https://github.com/anantkumarc/DriveSentinel.git
cd DriveSentinel
python3 setup_drive.py
```

After setup completes, start the phone server and power the ESP32 as described above. From the project folder, run:

```bash
source .venv/bin/activate
drive wifi PHONE_ADDRESS --ble
```

`setup_drive.py` creates the virtual environment, installs the application dependencies and BLE support, and downloads the SHA-256-verified model files. Python 3.11–3.14 is supported; if `py -3` selects another version, use a supported interpreter explicitly, for example `py -3.12 setup_drive.py`.

**Later runs:** skip the install and setup steps. Open a terminal in the `DriveSentinel` folder and use only the final run command (on macOS/Linux, `source .venv/bin/activate` first, once per new terminal).

**Arduino IDE** is only for the one-time ESP32 programming (macOS: `brew install --cask arduino-ide`; Linux: download from arduino.cc). It is not needed on a laptop if the ESP32 is already programmed.

---

## Other run modes

| Goal | Windows | macOS / Linux |
|---|---|---|
| Phone over Wi-Fi + ESP32 buzzer | `.venv\Scripts\drive.exe wifi PHONE_ADDRESS --ble` | `drive wifi PHONE_ADDRESS --ble` |
| Phone over Wi-Fi, laptop sound | `.venv\Scripts\drive.exe wifi PHONE_ADDRESS` | `drive wifi PHONE_ADDRESS` |
| Laptop webcam, laptop sound | `.venv\Scripts\drive.exe` | `drive` |
| Phone seen as USB webcam | `.venv\Scripts\drive.exe usb` | `drive usb` |

The phone address is saved on that laptop. If it changes, run the command again with the new one. With `--ble`, laptop sound is **off**; alerts use the ESP32 buzzer when it is connected and working. If BLE is unavailable, there is no laptop audio fallback. If the stream drops, DriveSentinel tries to reconnect.

---

## ESP32 buzzer

Program the ESP32-C3 once with the supplied Arduino sketch. Wire the buzzer using the [ESP32 wiring guide](docs/esp32-ble-buzzer.md) and check your buzzer's specifications first. The ESP32 uses the standard Bluetooth Immediate Alert Service, and its firmware turns the buzzer off if updates stop.

---

## Keys

| Key | Action |
|---|---|
| **F** | Fullscreen |
| **D** | Details |
| **N** | Reset head baseline (sit normally first) |
| **Q** | Quit |

---

## Default thresholds

| Cue | Value |
|---|---|
| Sustained head turn left / right / down | 3 seconds |
| Critical eye closure | 1.5 seconds |
| Phone presence | 1 second |

Configurable in the project YAML files. Head pose measures head direction, not eye gaze. Mouth opening is a candidate cue, not confirmed yawning. Phone detection means a phone is in the image region, not that it is being used. Hand movement alone never triggers the alarm.

---

## Quick fixes

| Problem | Fix |
|---|---|
| `py` not found | Reopen Command Prompt after installing Python |
| Wrong Python version | Need 64-bit 3.11 to 3.14 |
| Stream will not connect | Same hotspot/Wi-Fi, IP Webcam server running, address correct |
| ESP32 not found | Power it on, enable laptop Bluetooth, keep it close |
| No laptop sound | Expected with `--ble` |

---

## Status

**Verified:** clean install on Apple Silicon Mac, headless sample-video run, 66 automated tests passed, ESP32 BLE connection seen in a previous log.

**Not yet verified:** full Windows install and live demo, detection accuracy across people/cameras/lighting, buzzer latency on every ESP32 and buzzer combination, use as a standalone app or certified safety device.
