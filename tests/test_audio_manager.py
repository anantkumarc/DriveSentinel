from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

from dms.alerts.audio import AudioAlertManager, PlaybackState
from dms.alerts.states import AlertLevel, AlertStateMachine
from dms.fusion.base import RiskDecision, RiskState


class _FakeStream:
    active_count = 0
    max_active_count = 0
    lock = threading.Lock()

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.active = False
        self.closed = False
        self.start_count = 0

    def start(self) -> None:
        with self.lock:
            type(self).active_count += 1
            type(self).max_active_count = max(type(self).max_active_count, type(self).active_count)
        self.start_count += 1
        self.active = True

    def abort(self) -> None:
        self.active = False

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            with self.lock:
                type(self).active_count -= 1


class AudioAlertManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeStream.active_count = 0
        _FakeStream.max_active_count = 0
        self.streams: list[_FakeStream] = []

    def stream_factory(self, **kwargs: object) -> _FakeStream:
        stream = _FakeStream(**kwargs)
        self.streams.append(stream)
        return stream

    def make_manager(self) -> AudioAlertManager:
        return AudioAlertManager(
            enabled=True,
            sample_rate_hz=48_000,
            frequency_hz=2_400,
            volume=0.9,
            stream_factory=self.stream_factory,
        )

    @staticmethod
    def wait_until(predicate, timeout_s: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("timed out waiting for asynchronous audio state")

    def test_idle_is_silent_and_active_alerts_share_one_uninterrupted_stream(self) -> None:
        manager = self.make_manager()
        try:
            time.sleep(0.02)
            self.assertEqual(self.streams, [])

            manager.set_alert("high", "head-right-1")
            self.wait_until(lambda: manager.playback_state is PlaybackState.PLAYING_BUZZER)
            stream = self.streams[0]
            for index in range(100):
                manager.set_alert("high", f"episode-{index}")
            manager.set_alert("critical", "eye-closure-1")
            manager.set_alert("elevated", "yawn-1")
            time.sleep(0.03)

            self.assertEqual(len(self.streams), 1)
            self.assertEqual(stream.start_count, 1)
            self.assertTrue(stream.active)
            self.assertEqual(_FakeStream.active_count, 1)
            self.assertEqual(_FakeStream.max_active_count, 1)

            manager.set_alert("stopped")
            self.wait_until(lambda: manager.playback_state is PlaybackState.STOPPED)
            self.assertTrue(stream.closed)
            self.assertEqual(_FakeStream.active_count, 0)
        finally:
            manager.close()

    def test_same_active_state_does_not_restart_when_multiple_violations_change(self) -> None:
        manager = self.make_manager()
        try:
            manager.set_alert("high", "right-turn")
            self.wait_until(lambda: manager.playback_state is PlaybackState.PLAYING_BUZZER)
            original_stream = self.streams[0]
            manager.set_alert("high", "down-look-and-right-turn")
            manager.set_alert("critical", "simultaneous-eye-closure")
            time.sleep(0.03)
            self.assertIs(self.streams[0], original_stream)
            self.assertEqual(original_stream.start_count, 1)
            self.assertFalse(original_stream.closed)
        finally:
            manager.close()

    def test_buzzer_waveform_is_a_clear_nonclipping_2_4_khz_square_tone(self) -> None:
        manager = self.make_manager()
        try:
            self.assertAlmostEqual(manager.frequency_hz, 2_400, delta=1)
            output = np.zeros((1024, 1), dtype=np.float32)
            manager._audio_callback(output, len(output), None, None)
            self.assertGreater(float(output.max()), 0.85)
            self.assertLess(float(output.min()), -0.85)
            self.assertLessEqual(float(np.max(np.abs(output))), 0.9)
            spectrum = np.abs(np.fft.rfft(output[:, 0]))
            frequencies = np.fft.rfftfreq(len(output), d=1 / 48_000)
            strongest = float(frequencies[int(np.argmax(spectrum[1:]) + 1)])
            self.assertAlmostEqual(strongest, 2_400, delta=60)
        finally:
            manager.close()

    def test_no_output_device_is_reported_instead_of_claiming_audio_is_ready(self) -> None:
        class NoOutputDevice:
            default = SimpleNamespace(device=(-1, -1))

            @staticmethod
            def query_devices():
                return []

        with self.assertRaisesRegex(RuntimeError, "no audio output device"):
            AudioAlertManager._find_output_device(NoOutputDevice)

    def test_camera_shutdown_closes_the_audio_stream(self) -> None:
        manager = self.make_manager()
        manager.set_alert("high", "right-turn")
        self.wait_until(lambda: manager.playback_state is PlaybackState.PLAYING_BUZZER)
        stream = self.streams[0]
        manager.close()
        self.assertTrue(stream.closed)
        self.assertEqual(manager.playback_state, PlaybackState.STOPPED)
        self.assertEqual(_FakeStream.active_count, 0)

    def test_head_recovery_can_stop_audio_on_the_first_forward_observation(self) -> None:
        manager = self.make_manager()
        machine = AlertStateMachine(
            "distraction", entry_persistence_s=0.0, recovery_persistence_s=1.5, max_sample_gap_s=2.0
        )
        try:
            warning = RiskDecision(
                "distraction", RiskState.HIGH_CANDIDATE, 1.0,
                ("sustained_right_turn",), (), (), "rules",
            )
            active = machine.update(warning, 0.0)
            manager.set_alert(active.level.name.lower(), active.episode_id)
            self.wait_until(lambda: manager.playback_state is PlaybackState.PLAYING_BUZZER)

            forward = RiskDecision("distraction", RiskState.NO_TRIGGER, 0.0, (), (), (), "rules")
            recovered = machine.update(forward, 0.1, recovery_persistence_override_s=0.0)
            self.assertIs(recovered.level, AlertLevel.NONE)
            manager.set_alert(recovered.level.name.lower(), recovered.episode_id)
            self.wait_until(lambda: manager.playback_state is PlaybackState.STOPPED)
            self.assertTrue(self.streams[0].closed)
        finally:
            manager.close()


class AlertSeverityTests(unittest.TestCase):
    @staticmethod
    def decision(*, critical: bool = False, state: RiskState = RiskState.HIGH_CANDIDATE) -> RiskDecision:
        return RiskDecision(
            "drowsiness",
            state,
            1.0,
            ("critical_eye_closure" if critical else "high_condition",),
            (),
            (),
            "rules",
            critical,
        )

    def test_critical_alert_escalates_immediately_and_cannot_be_demoted_by_high(self) -> None:
        machine = AlertStateMachine(
            "drowsiness",
            entry_persistence_s=0.8,
            recovery_persistence_s=0.5,
            max_sample_gap_s=2.0,
        )
        high = machine.update(self.decision(), 0.0)
        self.assertIs(high.level, AlertLevel.NONE)
        critical = machine.update(self.decision(critical=True), 0.1)
        self.assertIs(critical.level, AlertLevel.CRITICAL)
        still_critical = machine.update(self.decision(), 0.2)
        self.assertIs(still_critical.level, AlertLevel.CRITICAL)

    def test_unknown_evidence_does_not_recover_and_safe_state_uses_persistence(self) -> None:
        machine = AlertStateMachine(
            "drowsiness",
            entry_persistence_s=0.0,
            recovery_persistence_s=0.5,
            max_sample_gap_s=3.0,
        )
        machine.update(self.decision(critical=True), 0.0)
        unknown = RiskDecision("drowsiness", RiskState.UNKNOWN, None, (), (), ("eyes",), "rules")
        self.assertIs(machine.update(unknown, 0.1).level, AlertLevel.CRITICAL)
        safe = RiskDecision("drowsiness", RiskState.NO_TRIGGER, 0.0, (), (), (), "rules")
        self.assertIs(machine.update(safe, 0.2).level, AlertLevel.CRITICAL)
        self.assertIs(machine.update(safe, 0.69).level, AlertLevel.CRITICAL)
        self.assertIs(machine.update(safe, 0.71).level, AlertLevel.NONE)

    def test_prevalidated_head_recovery_does_not_wait_through_a_second_hold(self) -> None:
        machine = AlertStateMachine(
            "distraction", entry_persistence_s=0.0, recovery_persistence_s=0.5, max_sample_gap_s=2.0
        )
        high = RiskDecision(
            "distraction", RiskState.HIGH_CANDIDATE, 1.0, ("sustained_left_turn",), (), (), "rules"
        )
        machine.update(high, 0.0)
        unknown = RiskDecision("distraction", RiskState.UNKNOWN, None, (), (), ("head",), "rules")
        self.assertIs(
            machine.update(unknown, 0.1, recovery_persistence_override_s=0.0).level,
            AlertLevel.HIGH,
        )
        safe = RiskDecision("distraction", RiskState.NO_TRIGGER, 0.0, (), (), (), "rules")
        recovered = machine.update(safe, 0.2, recovery_persistence_override_s=0.0)
        self.assertIs(recovered.level, AlertLevel.NONE)


if __name__ == "__main__":
    unittest.main()
