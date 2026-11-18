"""Repository-aware console launcher for the DriveSentinel application."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def phone_stream_url(address: str) -> str:
    """Turn the address displayed by IP Webcam into its MJPEG stream URL."""
    address = address.strip()
    if not address:
        raise ValueError("Enter the address shown by IP Webcam.")
    if "://" not in address:
        address = "http://" + address
    parts = urlsplit(address)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Enter an HTTP address without a username or password, for example 192.168.1.1:8080.")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("The address contains an invalid port.") from exc
    path = parts.path if parts.path not in {"", "/"} else "/video"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def project_root() -> Path:
    """Find the editable DriveSentinel checkout containing its full config."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "configs" / "full.yaml").is_file() and (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError(
        "Could not locate the DriveSentinel checkout. Install from the cloned "
        "repository with `python -m pip install -e .`."
    )


def main() -> int:
    """Launch the full profile and camera 0 from any current directory."""
    root = project_root()
    os.chdir(root)

    user_args = sys.argv[1:]
    if user_args and user_args[0] == "usb":
        usb_args = user_args[1:]
        number_arg = usb_args.pop(0) if usb_args and not usb_args[0].startswith("-") else None
        try:
            number = number_arg if number_arg is not None else input("Camera number (try 1 for your phone; 0 is often the laptop camera): ").strip()
            if not number.isdecimal():
                raise ValueError("Enter a nonnegative camera number, such as 1.")
            user_args = ["--source", str(int(number)), *usb_args]
        except (ValueError, EOFError, KeyboardInterrupt) as exc:
            print(f"Camera selection cancelled or invalid: {exc}", file=sys.stderr)
            return 2
    if user_args and user_args[0] == "wifi":
        wifi_args = user_args[1:]
        address_arg = wifi_args.pop(0) if wifi_args and not wifi_args[0].startswith("-") else None
        try:
            saved_path = root / "configs" / ".wifi-camera"
            if address_arg is not None:
                address = address_arg
            elif saved_path.is_file():
                address = saved_path.read_text(encoding="utf-8").strip()
                print("Connecting to the saved phone camera address. Use `drive wifi ADDRESS` to change it.")
            else:
                address = input("IP Webcam address (for example 192.168.1.1:8080): ")
            stream_url = phone_stream_url(address)
            if urlsplit(stream_url).query:
                raise ValueError("Camera URLs with query parameters cannot be saved; use --source instead.")
            saved_path.write_text(stream_url + "\n", encoding="utf-8")
            user_args = ["--source", stream_url, *wifi_args]
        except (ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
            print(f"Camera connection cancelled or invalid: {exc}", file=sys.stderr)
            return 2
    defaults: list[str] = []
    if not any(arg == "--config" or arg.startswith("--config=") for arg in user_args):
        defaults.extend(("--config", "configs/full.yaml"))
    sys.argv = [sys.argv[0], *defaults, *user_args]

    from dms.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
