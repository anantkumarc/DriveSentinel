# Contributing to DriveSentinel

Thanks for your interest in improving DriveSentinel. Contributions should keep
the project useful as a local research prototype and describe the evidence
behind changes to detection behavior.

## Development workflow

1. Fork the repository and clone your fork.
2. Create a virtual environment with Python 3.11 or later, below 3.15.
3. Install the project with `python -m pip install -e .` (or use
   `uv sync --locked`).
4. Create a feature branch, for example
   `git switch -c fix/head-pose-timing`.
5. Make a focused change and add or update tests for changed behavior.
6. Run the relevant checks:

   ```sh
   python -m unittest discover -s tests
   python tools/smoke.py --config configs/full.yaml --require-model
   ```

   The smoke check requires the face model described in
   [models/README.md](models/README.md).

7. Review the diff, then open a pull request describing the change, how it was
   checked, and any known limitations.

## Data and privacy

Do not submit credentials, private recordings, face images, datasets, local
calibration files, run logs, or downloaded model weights. Keep test fixtures
synthetic or otherwise approved for public distribution. Do not claim
real-world detection performance or safety from unit tests or staged demos.

## Detection changes

Preserve the distinction between measured cues and inferred behavior. For
example, sustained mouth opening is a yawn-like candidate, and detecting a
phone is not proof of driver phone use. Document threshold changes and include
tests for timing, recovery, and missing observations where applicable.
