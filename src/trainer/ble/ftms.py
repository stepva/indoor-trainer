"""FTMS (Fitness Machine Service) client for the trainer.

Parses Indoor Bike Data notifications and writes ERG target power to the
Fitness Machine Control Point.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

log = logging.getLogger(__name__)

# Standard FTMS UUIDs
FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
SUPPORTED_POWER_RANGE = "00002ad8-0000-1000-8000-00805f9b34fb"

# Control point op codes
OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_TARGET_POWER = 0x05
OP_START_RESUME = 0x07
OP_STOP_PAUSE = 0x08


@dataclass
class BikeSample:
    """One Indoor Bike Data notification, decoded."""
    speed_kph: float | None = None
    cadence_rpm: float | None = None
    power_w: int | None = None


def _parse_indoor_bike_data(data: bytes) -> BikeSample:
    """Parse an Indoor Bike Data characteristic payload.

    Layout: uint16 flags, then a sequence of optional fields. The first field
    (Instantaneous Speed) is present unless flag bit 0 is set ("more data").
    """
    if len(data) < 2:
        return BikeSample()
    flags = int.from_bytes(data[0:2], "little")
    i = 2
    out = BikeSample()

    # Bit 0 is "More Data" — when 0, Instantaneous Speed (uint16, 0.01 km/h) IS present.
    if not (flags & 0x0001):
        if i + 2 <= len(data):
            out.speed_kph = int.from_bytes(data[i:i + 2], "little") / 100.0
        i += 2
    # Bit 1: Average Speed
    if flags & 0x0002:
        i += 2
    # Bit 2: Instantaneous Cadence (uint16, 0.5 rpm)
    if flags & 0x0004:
        if i + 2 <= len(data):
            out.cadence_rpm = int.from_bytes(data[i:i + 2], "little") / 2.0
        i += 2
    # Bit 3: Average Cadence
    if flags & 0x0008:
        i += 2
    # Bit 4: Total Distance (uint24)
    if flags & 0x0010:
        i += 3
    # Bit 5: Resistance Level (sint16)
    if flags & 0x0020:
        i += 2
    # Bit 6: Instantaneous Power (sint16, watts)
    if flags & 0x0040:
        if i + 2 <= len(data):
            out.power_w = int.from_bytes(data[i:i + 2], "little", signed=True)
        i += 2
    # Remaining fields are ignored (avg power, energy, HR, met. equiv., elapsed, remaining time)
    return out


class FtmsClient:
    """Async wrapper around a BleakClient bound to an FTMS trainer."""

    def __init__(self) -> None:
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._on_sample: Callable[[BikeSample], None] | None = None
        self.min_w: int = 0
        self.max_w: int = 1000
        self.connected: bool = False

    @staticmethod
    async def scan(timeout: float = 6.0) -> list[BLEDevice]:
        """Find all advertising FTMS devices nearby."""
        log.info("Scanning for FTMS devices for %ss…", timeout)
        devices = await BleakScanner.discover(
            timeout=timeout, service_uuids=[FTMS_SERVICE]
        )
        return list(devices)

    async def connect(
        self,
        device: BLEDevice,
        on_sample: Callable[[BikeSample], None],
    ) -> None:
        self._on_sample = on_sample
        self._device = device
        self._client = BleakClient(device)
        await self._client.connect()
        self.connected = True
        log.info("Connected to %s", device.name or device.address)

        # Read supported power range if available (uint16 min, max, increment).
        try:
            raw = await self._client.read_gatt_char(SUPPORTED_POWER_RANGE)
            if len(raw) >= 4:
                lo, hi = struct.unpack_from("<hh", raw, 0)
                self.min_w, self.max_w = int(lo), int(hi)
                log.info("Trainer power range: %d–%d W", self.min_w, self.max_w)
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not read supported power range: %s", exc)

        await self._client.start_notify(
            INDOOR_BIKE_DATA, self._on_indoor_bike_data
        )

        # Take control of the trainer and tell it the workout is starting.
        await self._write_cp(bytes([OP_REQUEST_CONTROL]))
        await asyncio.sleep(0.2)
        await self._write_cp(bytes([OP_START_RESUME]))

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._write_cp(bytes([OP_STOP_PAUSE, 0x01]))  # 0x01 = stop
            except Exception:  # noqa: BLE001
                pass
            try:
                await self._client.stop_notify(INDOOR_BIKE_DATA)
            except Exception:  # noqa: BLE001
                pass
            await self._client.disconnect()
        self.connected = False

    async def set_target_power(self, watts: int) -> None:
        """Set the ERG-mode target power in whole watts."""
        clamped = max(self.min_w, min(self.max_w, int(watts)))
        payload = bytes([OP_SET_TARGET_POWER]) + struct.pack("<h", clamped)
        await self._write_cp(payload)

    async def _write_cp(self, payload: bytes) -> None:
        if not self._client:
            raise RuntimeError("Not connected")
        await self._client.write_gatt_char(
            FITNESS_MACHINE_CONTROL_POINT, payload, response=True
        )

    def _on_indoor_bike_data(self, _char, data: bytearray) -> None:
        sample = _parse_indoor_bike_data(bytes(data))
        if self._on_sample:
            self._on_sample(sample)
