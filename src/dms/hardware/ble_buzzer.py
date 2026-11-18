"""Non-blocking Bluetooth SIG Immediate Alert output for a single buzzer."""
from __future__ import annotations

import asyncio
import sys
import threading
import time

IAS_UUID = '00001802-0000-1000-8000-00805f9b34fb'
ALERT_UUID = '00002a06-0000-1000-8000-00805f9b34fb'


class BleBuzzer:
    """Refresh the standard alert level while the processing loop is alive."""

    def __init__(self, name: str = 'DriveSentinel-Buzzer', stale_s: float = 2.0):
        if stale_s <= 0:
            raise ValueError('BLE update timeout must be positive')
        self.name, self.stale_s = name, stale_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._updated = 0.0
        self._status = 'starting'
        self._loop = None
        self._task = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name='ble-buzzer')
        self._thread.start()

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = bool(active)
            self._updated = time.monotonic()

    @property
    def status(self) -> str:
        """Current BLE state; 'connected' means the alert characteristic was written."""
        with self._lock:
            return self._status

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def _desired_level(self) -> int:
        with self._lock:
            return 2 if self._active and time.monotonic() - self._updated < self.stale_s else 0

    def close(self, timeout_s: float = 3.0) -> None:
        self.set_active(False)
        self._stop.set()
        deadline = time.monotonic() + max(0.0, timeout_s)
        self._thread.join(min(0.75, max(0.0, timeout_s)))
        if self._thread.is_alive():
            with self._lock:
                loop, task = self._loop, self._task
            if loop is not None and task is not None:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass  # The loop completed concurrently.
            self._thread.join(max(0.0, deadline - time.monotonic()))

    def _worker(self) -> None:
        loop = asyncio.new_event_loop()
        task = loop.create_task(self._run())
        with self._lock:
            self._loop, self._task = loop, task
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._set_status('unavailable')
            print(f'BLE buzzer unavailable: {exc}', file=sys.stderr, flush=True)
        finally:
            with self._lock:
                self._loop, self._task = None, None
                if self._stop.is_set():
                    self._status = 'stopped'
            loop.close()

    async def _run(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self._set_status('unavailable')
            print('BLE buzzer unavailable: install the optional BLE dependency; see docs/esp32-ble-buzzer.md.', file=sys.stderr, flush=True)
            return
        while not self._stop.is_set():
            try:
                self._set_status('scanning')
                devices = await BleakScanner.discover(timeout=4.0, return_adv=True)
                if self._stop.is_set():
                    break
                matches = [device for device, adv in devices.values()
                           if (adv.local_name or device.name) == self.name
                           and IAS_UUID in [u.lower() for u in adv.service_uuids]]
                if len(matches) != 1:
                    self._set_status('disconnected')
                    if len(matches) > 1:
                        print(f'BLE buzzer: multiple devices named {self.name}; use unique names.', file=sys.stderr, flush=True)
                    await asyncio.sleep(1)
                    continue
                self._set_status('connecting')
                async with BleakClient(matches[0], timeout=8.0) as client:
                    try:
                        while not self._stop.is_set() and client.is_connected:
                            await client.write_gatt_char(ALERT_UUID, bytes([self._desired_level()]), response=False)
                            if self.status != 'connected':
                                self._set_status('connected')
                                print(f'BLE buzzer connected: {self.name}', file=sys.stderr, flush=True)
                            await asyncio.sleep(0.5)
                    finally:
                        self._set_status('disconnected')
                        if client.is_connected:
                            await asyncio.wait_for(client.write_gatt_char(ALERT_UUID, b'\x00', response=False), timeout=1.0)
                    if not self._stop.is_set():
                        print('BLE buzzer disconnected; reconnecting.', file=sys.stderr, flush=True)
            except Exception as exc:
                self._set_status('disconnected')
                if not self._stop.is_set():
                    print(f'BLE buzzer connection lost or unavailable: {exc}', file=sys.stderr, flush=True)
                    await asyncio.sleep(2)
