#!/usr/bin/env python3
"""Set up a DriveSentinel checkout on supported desktop systems."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_step(description: str, command: list[str]) -> None:
    print(f"\n{description}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if not (3, 11) <= sys.version_info[:2] < (3, 15):
        print("DriveSentinel requires Python 3.11–3.14; Python 3.11 is recommended.", file=sys.stderr)
        return 2
    venv = ROOT / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not python.is_file():
            if venv.exists() and any(venv.iterdir()):
                raise RuntimeError(".venv already exists but its Python executable is missing. Repair or rename it and retry.")
            run_step("Creating the project virtual environment...", [sys.executable, "-m", "venv", str(venv)])
        else:
            print("Using the existing project virtual environment.", flush=True)
        run_step("Checking the environment's Python version...", [str(python), "-c", "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)"])
        pip_check = subprocess.run([str(python), "-m", "pip", "--version"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pip_check.returncode:
            run_step("Adding pip to the existing environment...", [str(python), "-m", "ensurepip", "--upgrade"])
        run_step("Installing DriveSentinel and Bluetooth support...", [str(python), "-m", "pip", "install", "-e", ".[ble]"])
        run_step("Downloading and verifying the required models...", [str(python), "-m", "dms.launcher", "--download-models", "--all"])
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        print(f"\nSetup did not complete: {exc}", file=sys.stderr)
        print("Check the message above and your internet connection, then run this setup command again.", file=sys.stderr)
        return 1
    print("\nSetup complete. Activate the environment before running DriveSentinel:")
    if os.name == "nt":
        print(r"  .venv\Scripts\Activate.ps1")
    else:
        print("  source .venv/bin/activate")
    print("  drive")
    print("For the phone camera and ESP32 buzzer: drive wifi --ble")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
