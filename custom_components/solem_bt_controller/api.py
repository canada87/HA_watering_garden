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

from .const import CHARACTERISTIC_UUID, DEFAULT_BLUETOOTH_TIMEOUT, NOTIFY_UUID

_LOGGER = logging.getLogger(__name__)

COMMAND_COMMIT_DELAY = 0.3  # seconds between command and commit frame
NOTIFY_WAIT_SECONDS = 2.0  # seconds to wait for notification packets


class APIConnectionError(Exception):
    """BLE connection or write failure."""


def parse_state(raw_packets: list[bytearray]) -> dict:
    """Parse notification packets and extract device state.

    The device sends 2 groups of 3 packets (18 bytes each):
    - Group 1 (byte 0 = 0x32): state BEFORE command execution
    - Group 2 (byte 0 = 0x3C): state AFTER command execution
    Each group has 3 fragments identified by byte 2: 0x02 (main), 0x01, 0x00.

    Main fragment (byte 2 = 0x02) layout:
    - Byte 10: battery level (0-100 percentage)
    - Byte 13: active station — 0xFF = idle, 0xFN = station N active
    - Byte 14: countdown timer in seconds (0xFF = idle)
    """
    state: dict = {
        "battery_level": None,
        "active_station": None,
        "is_irrigating": False,
        "raw_packets": [p.hex() for p in raw_packets],
    }

    # Prefer the "after" group (0x3C) for most up-to-date state
    for marker in (0x3C, 0x32):
        for packet in raw_packets:
            if len(packet) >= 15 and packet[0] == marker and packet[2] == 0x02:
                state["battery_level"] = packet[10]

                station_byte = packet[13]
                countdown = packet[14]

                if station_byte != 0xFF and countdown != 0xFF and countdown > 0:
                    state["active_station"] = station_byte & 0x0F
                    state["is_irrigating"] = True

                _LOGGER.debug(
                    "Parsed state: battery=%d%%, station_byte=0x%02X, "
                    "countdown=%d, is_irrigating=%s, active_station=%s",
                    packet[10], station_byte, countdown,
                    state["is_irrigating"], state["active_station"],
                )
                return state

    _LOGGER.debug("No parseable state found in %d packets", len(raw_packets))
    return state


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
        self.last_rssi: int | None = None

    async def _resolve_ble_device(self):
        """Find the BLE device by MAC address."""
        _LOGGER.debug("Resolving BLE device %s ...", self.mac_address)
        device = await BleakScanner.find_device_by_address(
            self.mac_address, timeout=5.0
        )
        if device:
            _LOGGER.debug("Device found via find_device_by_address: %s", device)
            rssi = getattr(device, "rssi", None)
            if rssi and rssi != 0:
                self.last_rssi = rssi
                _LOGGER.debug("RSSI: %d dB", rssi)
            return device

        # Fallback: full scan with manual match
        _LOGGER.debug("Fallback: full BLE scan for %s", self.mac_address)
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if d.address and d.address.upper() == self.mac_address.upper():
                _LOGGER.debug("Device found via full scan: %s", d)
                rssi = getattr(d, "rssi", None)
                if rssi and rssi != 0:
                    self.last_rssi = rssi
                    _LOGGER.debug("RSSI: %d dB", rssi)
                return d

        raise APIConnectionError(f"Device {self.mac_address} not found")

    async def _connect_client(self) -> BleakClient:
        """Establish a BLE connection with retry."""
        try:
            ble_device = await self._resolve_ble_device()
            _LOGGER.debug("Establishing connection to %s ...", ble_device)
            client = await establish_connection(
                BleakClient,
                ble_device,
                name=f"Solem - {self.mac_address}",
                timeout=self.bluetooth_timeout,
                max_attempts=3,
            )
            _LOGGER.debug("Connected. MTU size: %s", client.mtu_size)
            return client
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
    async def _write_with_retry(
        self, client: BleakClient, payload: bytes, response: bool = True,
    ) -> None:
        """Write a GATT characteristic with tenacity retry."""
        _LOGGER.debug(
            "Writing %d bytes to %s (response=%s): %s",
            len(payload), CHARACTERISTIC_UUID, response, payload.hex(),
        )
        await client.write_gatt_char(CHARACTERISTIC_UUID, payload, response=response)
        _LOGGER.debug("Write OK")

    async def _send_command(self, command: bytes) -> dict:
        """Connect, subscribe to notify, send command+commit, read state, disconnect.

        Returns parsed state dict with at least 'battery_level' key.
        """
        commit = struct.pack(">BB", 0x3B, 0x00)
        received: list[bytearray] = []

        def _on_notify(_sender, data: bytearray) -> None:
            received.append(bytearray(data))

        async with self._conn_lock:
            client = await self._connect_client()
            try:
                # Subscribe to notifications (best-effort — command still sent on failure)
                subscribed = False
                try:
                    await client.start_notify(NOTIFY_UUID, _on_notify)
                    subscribed = True
                except Exception as ex:
                    _LOGGER.warning("Could not subscribe to notifications: %s", ex)

                # Send command + commit
                await self._write_with_retry(client, command)
                await asyncio.sleep(COMMAND_COMMIT_DELAY)
                await self._write_with_retry(client, commit)
                _LOGGER.debug("Command + commit sent successfully")

                # Wait for notification packets
                if subscribed:
                    await asyncio.sleep(NOTIFY_WAIT_SECONDS)
                    try:
                        await client.stop_notify(NOTIFY_UUID)
                    except Exception:  # noqa: BLE001
                        pass

            except Exception as ex:
                _LOGGER.error(
                    "Send command failed (command=%s): %s", command.hex(), ex,
                )
                raise
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        if received:
            _LOGGER.debug("Received %d notification packets", len(received))
            return parse_state(received)
        return {"battery_level": None, "raw_packets": []}

    # -- Public commands (all return parsed state dict) --

    async def sprinkle_station(self, station: int, minutes: int) -> dict:
        """Start irrigation on a single station. Returns device state."""
        station = max(1, min(16, station))
        minutes = max(1, min(240, minutes))
        command = struct.pack(
            ">HBBBBH", 0x3105, 0x22, station, 0x00, minutes, 0xFFFF
        )
        _LOGGER.debug(
            "Sprinkle station %d for %d min — payload: %s",
            station, minutes, command.hex(),
        )
        return await self._send_command(command)

    async def stop_manual_sprinkle(self) -> dict:
        """Stop all manual irrigation. Returns device state."""
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        _LOGGER.debug("Stop manual sprinkle — payload: %s", command.hex())
        return await self._send_command(command)

    async def stop_manual_sprinkle_repeated(self, attempts: int = 3) -> dict:
        """Send stop command multiple times in a single connection for reliability.

        Returns device state from the final set of notifications.
        """
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        commit = struct.pack(">BB", 0x3B, 0x00)
        received: list[bytearray] = []

        def _on_notify(_sender, data: bytearray) -> None:
            received.append(bytearray(data))

        _LOGGER.debug(
            "Stop manual sprinkle (repeated %dx) — payload: %s",
            attempts, command.hex(),
        )
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                # Subscribe to notifications
                try:
                    await client.start_notify(NOTIFY_UUID, _on_notify)
                except Exception as ex:
                    _LOGGER.warning("Could not subscribe to notifications: %s", ex)

                for attempt in range(1, attempts + 1):
                    _LOGGER.debug("Stop attempt %d/%d", attempt, attempts)
                    await self._write_with_retry(client, command)
                    await asyncio.sleep(COMMAND_COMMIT_DELAY)
                    await self._write_with_retry(client, commit)
                    if attempt < attempts:
                        await asyncio.sleep(1.0)

                _LOGGER.debug("Stop command sent %d times successfully", attempts)
                await asyncio.sleep(NOTIFY_WAIT_SECONDS)

                try:
                    await client.stop_notify(NOTIFY_UUID)
                except Exception:  # noqa: BLE001
                    pass

            except Exception as ex:
                _LOGGER.error("Stop repeated write failed: %s", ex)
                raise
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        # Parse state from the last group of notifications
        if received:
            _LOGGER.debug("Received %d notification packets from stop", len(received))
            return parse_state(received)
        return {"battery_level": None, "raw_packets": []}

    async def turn_on(self) -> dict:
        """Turn on the controller. Returns device state."""
        command = struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)
        _LOGGER.debug("Turn on controller — payload: %s", command.hex())
        return await self._send_command(command)

    async def turn_off_permanent(self) -> dict:
        """Turn off the controller permanently. Returns device state."""
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)
        _LOGGER.debug("Turn off controller permanently — payload: %s", command.hex())
        return await self._send_command(command)

    async def read_state(self) -> dict:
        """Read device state by sending a turn-on command (safe if already on).

        Note: this will turn on the controller if it was off.
        """
        _LOGGER.debug("Reading device state via turn-on command")
        return await self.turn_on()

    async def list_characteristics(self) -> dict:
        """List all GATT services and characteristics (for config flow test)."""
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
