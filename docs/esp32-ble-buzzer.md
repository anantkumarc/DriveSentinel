# ESP32-C3 BLE buzzer

The Mac runs DriveSentinel and the ESP32 runs the Arduino sketch in `firmware/DriveSentinel_BLE`. The Bluetooth SIG Immediate Alert service (`1802`) and Alert Level characteristic (`2A06`) use write without response: 0 = off, 1 = mild, 2 = high. DriveSentinel uses 0 and 2. The ESP32 uses a four-second watchdog as an application-specific safety extension; the Mac refreshes the current level twice per second. A disconnect, stale stream of commands, reset, or lost camera loop turns the buzzer off. Bluetooth write without response does not confirm that the buzzer physically sounded. Laptop audio is disabled when `--ble` is selected.

## Wiring

Use an ESP32-C3 SuperMini powered by its USB port, a 3.3 V **active** two-pin buzzer, an NPN transistor such as a BC337 or 2N2222, a 1 kΩ base resistor, and a 100 kΩ base-to-ground resistor. Check the exact transistor pinout and the buzzer polarity and current in their data sheets. Check that the board's 3.3 V regulator and transistor can supply the buzzer's rated current; 3.3 V by itself does not specify current. Do not connect an unknown load directly to a GPIO.

```text
ESP32 3V3 ───────── buzzer (+)
                      buzzer (−) ─── NPN collector
ESP32 GPIO4 ── 1 kΩ ─────────────── NPN base
                                       │
                                    100 kΩ
                                       │
ESP32 GND ───────────────────────────┴── NPN emitter
```

For a magnetic or inductive buzzer, add a suitable flyback diode across the buzzer, cathode (striped end) to 3V3 and anode to buzzer negative/collector. Confirm the buzzer type from its data sheet; the photo alone does not establish this. SuperMini variants differ; confirm GPIO4 and 3V3/GND labels on your actual board. Disconnect USB power while wiring. The bare active buzzer contains an oscillator; a passive buzzer would require different drive firmware.

## Arduino IDE

1. Install the Espressif **esp32** boards package through Arduino IDE Boards Manager. Select an ESP32-C3 board definition matching your SuperMini (the generic ESP32C3 Dev Module is a starting point). Use a USB data cable and choose the correct port. The built-in ESP32 BLE library is used; no custom BLE UUIDs are needed.
2. Open `firmware/DriveSentinel_BLE/DriveSentinel_BLE.ino` in Arduino IDE and upload it. If your board needs it, enable USB CDC On Boot and use its BOOT procedure for the first upload. The serial monitor at 115200 shows `DriveSentinel BLE ready` after reset.
3. On the Mac, turn Bluetooth on and allow Bluetooth access when macOS requests it. Keep one powered ESP32 advertising as `DriveSentinel-Buzzer`; change the firmware name and `--ble-name` for multiple devices.
4. Install the optional dependency once: `uv pip install --python .venv/bin/python 'bleak>=0.22,<4'`. On a normal pip-based environment, `python -m pip install -e '.[ble]'` is another option.
5. Start your phone stream and run `drive wifi --ble`. For a local webcam use `drive --ble`. Terminal reports a connection; test with the vehicle stationary. If BLE is unavailable, laptop audio remains disabled while `--ble` is selected. Press Q to close it.

Use a trusted environment: this initial firmware advertises openly and does not authenticate BLE clients. Any nearby BLE client can trigger the buzzer while connected. There is no claim of automotive certification or validated alert delivery. A BLE loss or watchdog expiry deliberately silences the buzzer and cannot indicate a working monitor; laptop audio is disabled in BLE mode. On macOS, BLE device identifiers are OS-specific; the client scans by name and standard advertised service, and refuses to choose if it sees multiple matches.
