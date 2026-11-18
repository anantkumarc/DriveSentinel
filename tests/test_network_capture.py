import time
import unittest
from unittest.mock import patch

import numpy as np
import cv2

from dms.capture.backend import CaptureBackend


class FakeCapture:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.opened = True

    def isOpened(self):
        return self.opened

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return 4
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return 2
        return 30

    def getBackendName(self):
        return 'fake'

    def read(self):
        try:
            return True, next(self.frames).copy()
        except StopIteration:
            return False, None

    def release(self):
        self.opened = False


class NetworkCaptureTests(unittest.TestCase):
    def test_rotation_and_reconnect_after_lost_stream(self):
        frame = np.zeros((2, 4, 3), dtype=np.uint8)
        frame[0, 0] = (1, 2, 3)
        first = FakeCapture([frame])
        second = FakeCapture([frame])
        with patch('cv2.VideoCapture', side_effect=[first, second]) as open_capture:
            capture = CaptureBackend('http://example.invalid/video', rotation=90)
            capture.start()
            try:
                packet = capture.read_newer(0, timeout_s=2)
                self.assertIsNotNone(packet)
                self.assertEqual(packet.image.shape, (4, 2, 3))
                info = capture.snapshot().info
                self.assertIsNotNone(info)
                self.assertEqual((info.width, info.height), (2, 4))
                np.testing.assert_array_equal(packet.image[0, 1], [1, 2, 3])
                deadline = time.monotonic() + 4
                next_packet = None
                while time.monotonic() < deadline and next_packet is None:
                    next_packet = capture.read_newer(packet.sequence, timeout_s=0.2)
                self.assertIsNotNone(next_packet, 'stream did not reconnect')
                self.assertGreater(next_packet.source_time_s, packet.source_time_s)
                for call in open_capture.call_args_list:
                    self.assertEqual(call.args[1], cv2.CAP_FFMPEG)
                    self.assertEqual(call.args[2], [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 2500,
                                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500])
            finally:
                capture.stop()

    def test_rotation_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            CaptureBackend(0, rotation=45)

    def test_metadata_uses_actual_frame_dimensions(self):
        frame = np.zeros((3, 6, 3), dtype=np.uint8)
        with patch('cv2.VideoCapture', return_value=FakeCapture([frame])):
            capture = CaptureBackend('http://example.invalid/video')
            capture.start()
            try:
                self.assertIsNotNone(capture.read_newer(0, timeout_s=2))
                info = capture.snapshot().info
                self.assertEqual((info.width, info.height), (6, 3))
            finally:
                capture.stop()
