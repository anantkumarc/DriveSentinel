"""Thread-safe continuous piezo-style buzzer for active driver warnings."""

from __future__ import annotations

import math
import threading
from enum import Enum
from typing import Any, Callable


class PlaybackState(str, Enum):
    STOPPED = "STOPPED"
    PLAYING_BUZZER = "PLAYING_BUZZER"
    # Compatibility aliases for clients that used the former per-severity WAV
    # states. All alert levels now share one continuous buzzer stream.
    PLAYING_ELEVATED = "PLAYING_BUZZER"
    PLAYING_HIGH = "PLAYING_BUZZER"
    PLAYING_CRITICAL = "PLAYING_BUZZER"


class AudioAlertManager:
    """Own one PortAudio stream and synthesize a continuous square-wave tone.

    ``set_alert`` is safe to call on every camera frame. It only changes the
    desired active flag when that flag changes, so the stream is not reopened
    as alert episode IDs or severity levels update. The output callback copies
    a precomputed, band-limited square wave and does no per-frame DSP work.
    """

    def __init__(
        self,
        sound_paths: Any = None,
        *,
        enabled: bool = False,
        sample_rate_hz: int = 48_000,
        frequency_hz: float = 2_400.0,
        volume: float = 0.9,
        blocksize: int = 512,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        del sound_paths  # Retained as a positional compatibility argument.
        if sample_rate_hz < 8_000:
            raise ValueError("audio sample rate must be at least 8000 Hz")
        if not math.isfinite(frequency_hz) or not 2_000.0 <= frequency_hz <= 3_000.0:
            raise ValueError("piezo buzzer frequency must be between 2000 and 3000 Hz")
        if not math.isfinite(volume) or not 0.0 < volume <= 1.0:
            raise ValueError("buzzer volume must be greater than zero and at most one")
        if blocksize < 0:
            raise ValueError("audio blocksize cannot be negative")

        self.enabled = bool(enabled)
        self.sample_rate_hz = int(sample_rate_hz)
        self.requested_frequency_hz = float(frequency_hz)
        self.frequency_hz = self.requested_frequency_hz
        self.volume = float(volume)
        self.blocksize = int(blocksize)
        self._condition = threading.Condition()
        self._desired_active = False
        self._playback_state = PlaybackState.STOPPED
        self._stream: Any | None = None
        self._closed = False
        self._error: str | None = None
        self._phase = 0
        self._underrun_count = 0
        self._numpy: Any | None = None
        self._wave_cycle: Any | None = None
        self._stream_factory = stream_factory
        self._output_device: int | None = None
        self._thread: threading.Thread | None = None

        if self.enabled:
            try:
                import numpy as np
                import sounddevice as sd

                self._numpy = np
                self._wave_cycle = self._make_bandlimited_square_wave(np)
                self.frequency_hz = self.sample_rate_hz / len(self._wave_cycle)
                if self._stream_factory is None:
                    self._output_device = self._find_output_device(sd)
                    self._stream_factory = sd.OutputStream
            except Exception as exc:
                self._error = f"piezo audio initialization failed: {type(exc).__name__}: {exc}"
            else:
                self._thread = threading.Thread(
                    target=self._worker,
                    name="dms-piezo-buzzer-controller",
                    daemon=True,
                )
                self._thread.start()

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        return "failed" if self._error else "ready"

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def playback_state(self) -> PlaybackState:
        with self._condition:
            return self._playback_state

    @property
    def active(self) -> bool:
        with self._condition:
            return self._desired_active

    @property
    def underrun_count(self) -> int:
        with self._condition:
            return self._underrun_count

    def set_alert(self, level: str | Enum, episode_id: str | None = None) -> bool:
        """Request continuous tone for any active alert without blocking."""
        del episode_id
        if not self.enabled or self._error or self._thread is None:
            return False
        raw_level = level.name.lower() if isinstance(level, Enum) else str(level).casefold()
        if raw_level in {"none", "stopped", "0"}:
            desired_active = False
        elif raw_level in {"elevated", "high", "critical", "playing_buzzer", "active", "on"}:
            desired_active = True
        else:
            raise ValueError(f"unsupported audio alert level: {level}")
        with self._condition:
            if self._closed:
                return False
            if self._desired_active != desired_active:
                self._desired_active = desired_active
                self._condition.notify_all()
        return True

    def set_active(self, active: bool) -> bool:
        return self.set_alert("high" if active else "stopped")

    def close(self, timeout_s: float = 2.0) -> None:
        with self._condition:
            self._desired_active = False
            self._closed = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(max(timeout_s, 0.0))
            if self._thread.is_alive():
                self._error = "piezo audio controller did not stop before timeout"

    def _worker(self) -> None:
        stream: Any | None = None
        try:
            while True:
                with self._condition:
                    while (
                        not self._closed
                        and self._desired_active == (stream is not None)
                    ):
                        self._condition.wait(timeout=0.5)
                        if stream is not None and not self._closed and self._desired_active:
                            if not bool(getattr(stream, "active", True)):
                                raise RuntimeError("audio output stream stopped unexpectedly")
                    closed = self._closed
                    desired_active = self._desired_active and not closed

                if stream is not None and not desired_active:
                    self._stop_stream(stream)
                    stream = None
                    with self._condition:
                        self._stream = None
                        self._playback_state = PlaybackState.STOPPED
                    if closed:
                        break
                    continue

                if stream is None and desired_active:
                    assert self._stream_factory is not None
                    self._phase = 0  # Begin each episode on the waveform's zero crossing.
                    stream = self._stream_factory(
                        samplerate=self.sample_rate_hz,
                        channels=1,
                        dtype="float32",
                        blocksize=self.blocksize,
                        latency="low",
                        device=self._output_device,
                        callback=self._audio_callback,
                    )
                    stream.start()
                    with self._condition:
                        self._stream = stream
                        self._playback_state = PlaybackState.PLAYING_BUZZER
                    continue

                if closed:
                    break
        except Exception as exc:
            self._error = f"piezo audio playback failed: {type(exc).__name__}: {exc}"
        finally:
            if stream is not None:
                try:
                    self._stop_stream(stream)
                except Exception as exc:
                    if self._error is None:
                        self._error = f"piezo audio cleanup failed: {type(exc).__name__}: {exc}"
            with self._condition:
                self._stream = None
                self._desired_active = False
                self._playback_state = PlaybackState.STOPPED
                self._condition.notify_all()

    def _audio_callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        del time_info
        cycle = self._wave_cycle
        if status:
            with self._condition:
                self._underrun_count += 1
        if cycle is None:
            outdata.fill(0.0)
            return
        cycle_length = len(cycle)
        written = 0
        while written < frames:
            count = min(frames - written, cycle_length - self._phase)
            outdata[written : written + count, 0] = cycle[self._phase : self._phase + count]
            written += count
            self._phase = (self._phase + count) % cycle_length

    def _make_bandlimited_square_wave(self, np: Any) -> Any:
        period = max(2, int(round(self.sample_rate_hz / self.requested_frequency_hz)))
        fundamental_hz = self.sample_rate_hz / period
        sample_positions = np.arange(period, dtype=np.float64)
        waveform = np.zeros(period, dtype=np.float64)
        harmonic_limit = int((self.sample_rate_hz / 2) // fundamental_hz)
        for harmonic in range(1, harmonic_limit + 1, 2):
            waveform += np.sin(2.0 * np.pi * harmonic * sample_positions / period) / harmonic
        peak = float(np.max(np.abs(waveform)))
        if peak <= 0.0:
            raise ValueError("generated piezo waveform is silent")
        waveform *= self.volume / peak
        return waveform.astype(np.float32)

    @staticmethod
    def _find_output_device(sd: Any) -> int:
        devices = sd.query_devices()
        default_devices = sd.default.device
        raw_default_output = default_devices[1] if default_devices is not None else None
        default_output = int(raw_default_output) if raw_default_output is not None else -1
        if 0 <= default_output < len(devices) and int(devices[default_output]["max_output_channels"]) > 0:
            return default_output
        for index, device in enumerate(devices):
            if int(device["max_output_channels"]) > 0:
                return index
        raise RuntimeError("no audio output device is available to PortAudio")

    @staticmethod
    def _stop_stream(stream: Any) -> None:
        # abort() discards queued output and resolves the warning promptly;
        # stop() is the fallback for compatible output-stream implementations.
        abort = getattr(stream, "abort", None)
        try:
            if callable(abort):
                abort()
            else:
                stream.stop()
        finally:
            stream.close()


class AudioNotifier(AudioAlertManager):
    """Compatibility wrapper for callers that previously supplied a WAV."""

    def __init__(self, sound_path: Any = None, enabled: bool = False) -> None:
        super().__init__(sound_path, enabled=enabled)
