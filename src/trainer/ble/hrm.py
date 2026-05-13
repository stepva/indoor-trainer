"""Standard BLE Heart Rate Service client.

Works with any device broadcasting the HR profile, including Garmin watches
in "Broadcast HR" mode and chest straps.
"""
from __future__ import annotations

import logging
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

log = logging.getLogger(__name__)

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def _parse_hr(data: bytes) -> int | None:
    """Decode an HR Measurement notification → BPM."""
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:
        # 16-bit value
        if len(data) >= 3:
            return int.from_bytes(data[1:3], "little")
        return None
    # 8-bit value
    if len(data) >= 2:
        return data[1]
    return None


class HrmClient:
    def __init__(self) -> None:
        self._client: BleakClient | None = None
        self._on_hr: Callable[[int], None] | None = None
        self.connected: bool = False

    @staticmethod
    async def scan(timeout: float = 6.0) -> list[BLEDevice]:
        log.info("Scanning for HR devices for %ss…", timeout)
        devices = await BleakScanner.discover(
            timeout=timeout, service_uuids=[HR_SERVICE]
        )
        return list(devices)

    async def connect(self, device: BLEDevice, on_hr: Callable[[int], None]) -> None:
        self._on_hr = on_hr
        self._client = BleakClient(device)
        await self._client.connect()
        self.connected = True
        log.info("HR connected to %s", device.name or device.address)
        await self._client.start_notify(HR_MEASUREMENT, self._on_notify)

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(HR_MEASUREMENT)
            except Exception:  # noqa: BLE001
                pass
            await self._client.disconnect()
        self.connected = False

    def _on_notify(self, _char, data: bytearray) -> None:
        bpm = _parse_hr(bytes(data))
        if bpm is not None and self._on_hr:
            self._on_hr(bpm)
