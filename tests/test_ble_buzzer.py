import asyncio
import sys
import threading
import unittest
from unittest.mock import patch
from dms.hardware.ble_buzzer import BleBuzzer, ALERT_UUID, IAS_UUID


class BleBuzzerTests(unittest.TestCase):
    def test_expired_processing_update_clears_alert(self):
        buzzer = BleBuzzer.__new__(BleBuzzer)
        buzzer._lock = threading.Lock()
        buzzer._status = 'starting'
        buzzer._active = False
        buzzer._updated = 0.0
        buzzer.stale_s = 2.0
        with patch('dms.hardware.ble_buzzer.time.monotonic', return_value=100.0):
            buzzer.set_active(True)
            self.assertEqual(buzzer._desired_level(), 2)
        with patch('dms.hardware.ble_buzzer.time.monotonic', return_value=102.1):
            self.assertEqual(buzzer._desired_level(), 0)

    def test_standard_write_and_shutdown_clear(self):
        buzzer = BleBuzzer.__new__(BleBuzzer)
        buzzer.name = 'DriveSentinel-Buzzer'
        buzzer.stale_s = 2
        buzzer._stop = threading.Event()
        buzzer._lock = threading.Lock()
        buzzer._status = 'starting'
        buzzer._active = True
        import time
        buzzer._updated = time.monotonic()
        writes = []
        class Device:
            name = 'DriveSentinel-Buzzer'
        class Adv:
            local_name = 'DriveSentinel-Buzzer'
            service_uuids = [IAS_UUID]
        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                return {'address': (Device(), Adv())}
        class Client:
            is_connected = True
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def write_gatt_char(self, uuid, data, response):
                writes.append((uuid, data, response))
                buzzer._stop.set()
        with patch.dict(sys.modules, {'bleak': type('Bleak', (), {'BleakClient': Client, 'BleakScanner': Scanner})}):
            asyncio.run(buzzer._run())
        self.assertEqual(writes, [(ALERT_UUID, b'\x02', False), (ALERT_UUID, b'\x00', False)])

    def test_close_cancels_pending_scan_without_connecting(self):
        import time
        scanning = threading.Event()
        cancelled = threading.Event()
        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                scanning.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.set()
        class Client:
            def __init__(self, *args, **kwargs):
                raise AssertionError('closed scanner attempted connection')
        with patch.dict(sys.modules, {'bleak': type('Bleak', (), {'BleakClient': Client, 'BleakScanner': Scanner})}):
            buzzer = BleBuzzer()
            self.assertTrue(scanning.wait(2))
            self.assertEqual(buzzer.status, 'scanning')
            start = time.monotonic()
            buzzer.close(timeout_s=2)
            self.assertLess(time.monotonic() - start, 2.0)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(buzzer._thread.is_alive())
            self.assertEqual(buzzer.status, 'stopped')

    def test_shutdown_during_scan_does_not_start_connection(self):
        buzzer = BleBuzzer.__new__(BleBuzzer)
        buzzer.name = 'DriveSentinel-Buzzer'
        buzzer.stale_s = 2
        buzzer._stop = threading.Event()
        buzzer._lock = threading.Lock()
        buzzer._status = 'starting'
        class Device:
            name = 'DriveSentinel-Buzzer'
        class Adv:
            local_name = 'DriveSentinel-Buzzer'
            service_uuids = [IAS_UUID]
        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                buzzer._stop.set()
                return {'address': (Device(), Adv())}
        class Client:
            def __init__(self, *args, **kwargs):
                raise AssertionError('connected after shutdown')
        with patch.dict(sys.modules, {'bleak': type('Bleak', (), {'BleakClient': Client, 'BleakScanner': Scanner})}):
            asyncio.run(buzzer._run())
