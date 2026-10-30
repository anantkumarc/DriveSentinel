"""Repository-aware console launcher for the DriveSentinel application."""

from __future__ import annotations

import os
import sys
from pathlib import Path


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
    defaults: list[str] = []
    if not any(arg == "--config" or arg.startswith("--config=") for arg in user_args):
        defaults.extend(("--config", "configs/full.yaml"))
    if not any(arg == "--source" or arg.startswith("--source=") for arg in user_args):
        defaults.extend(("--source", "0"))
    sys.argv = [sys.argv[0], *defaults, *user_args]

    from dms.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
