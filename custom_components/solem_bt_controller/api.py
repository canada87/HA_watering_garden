"""BLE API for Solem BL-IP controller. Internalized from solem_toolkit."""

import asyncio
import logging
import struct

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakDBusError
from bleak_retry_connector import (
    BleakOutOfConnectionSlotsError,
    establish_connection,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from .const import CHARACTERISTIC_UUID, DEFAULT_BLUETOOTH_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class APIConnectionError(Exception):
    """BLE connection or write failure."""


class SolemBleApi:
    """Low-level BLE communication with Solem BL-IP."""

    def __init__(
        self,
        mac_address: str,
        bluetooth_timeout: int = DEFAULT_BLUETOOTH_TIMEOUT,
    ) -> None:
        self.mac_address = mac_address
        self.bluetooth_timeout = bluetooth_timeout
        self._conn_lock = asyncio.Lock()

    async def _resolve_ble_device(self):
        """Find the BLE device by MAC address."""
        device = await BleakScanner.find_device_by_address(
            self.mac_address, timeout=5.0
        )
        if device:
            return device

        # Fallback: full scan with manual match
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if d.address and d.address.upper() == self.mac_address.upper():
                return d

        raise APIConnectionError(f"Device {self.mac_address} not found")

    async def _connect_client(self) -> BleakClient:
        """Establish a BLE connection with retry."""
        try:
            ble_device = await self._resolve_ble_device()
            return await establish_connection(
                BleakClient,
                ble_device,
                name=f"Solem - {self.mac_address}",
                timeout=self.bluetooth_timeout,
                max_attempts=3,
            )
        except BleakOutOfConnectionSlotsError as ex:
            raise APIConnectionError(f"Out of connection slots: {ex}") from ex
        except BleakDBusError as ex:
            raise APIConnectionError(f"DBus error: {ex}") from ex
        except TimeoutError as ex:
            raise APIConnectionError(f"Connection timeout: {ex}") from ex
        except OSError as ex:
            raise APIConnectionError(f"OS error: {ex}") from ex
        except APIConnectionError:
            raise
        except Exception as ex:
            raise APIConnectionError(f"Connection failed: {ex}") from ex

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.4, min=0.4, max=2),
    )
    async def _write_with_retry(self, client: BleakClient, payload: bytes) -> None:
        """Write a GATT characteristic with tenacity retry."""
        await client.write_gatt_char(CHARACTERISTIC_UUID, payload, response=False)

    async def _write_and_commit(self, command: bytes) -> None:
        """Connect, write command + commit frame, disconnect."""
        commit = struct.pack(">BB", 0x3B, 0x00)
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                await self._write_with_retry(client, command)
                await self._write_with_retry(client, commit)
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    # -- Public commands --

    async def sprinkle_station(self, station: int, minutes: int) -> None:
        """Start irrigation on a single station."""
        station = max(1, min(16, station))
        minutes = max(1, min(240, minutes))
        command = struct.pack(
            ">HBBBBH", 0x3105, 0x22, station, 0x00, minutes, 0xFFFF
        )
        _LOGGER.debug("Sprinkle station %d for %d min", station, minutes)
        await self._write_and_commit(command)

    async def stop_manual_sprinkle(self) -> None:
        """Stop all manual irrigation."""
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        _LOGGER.debug("Stop manual sprinkle")
        await self._write_and_commit(command)

    async def turn_on(self) -> None:
        """Turn on the controller."""
        command = struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)
        _LOGGER.debug("Turn on controller")
        await self._write_and_commit(command)

    async def turn_off_permanent(self) -> None:
        """Turn off the controller permanently."""
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)
        _LOGGER.debug("Turn off controller permanently")
        await self._write_and_commit(command)

    async def list_characteristics(self) -> dict:
        """List all GATT services and characteristics (for debugging)."""
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                result = {}
                for service in client.services:
                    chars = []
                    for char in service.characteristics:
                        chars.append(
                            {"uuid": char.uuid, "properties": char.properties}
                        )
                    result[service.uuid] = chars
                return result
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
