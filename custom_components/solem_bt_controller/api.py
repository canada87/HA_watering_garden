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
NOTIFY_WAIT_SECONDS = 3.0  # seconds to wait for notifications after a command


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
        _LOGGER.debug("Resolving BLE device %s ...", self.mac_address)
        device = await BleakScanner.find_device_by_address(
            self.mac_address, timeout=5.0
        )
        if device:
            _LOGGER.debug("Device found via find_device_by_address: %s", device)
            return device

        # Fallback: full scan with manual match
        _LOGGER.debug("Fallback: full BLE scan for %s", self.mac_address)
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if d.address and d.address.upper() == self.mac_address.upper():
                _LOGGER.debug("Device found via full scan: %s", d)
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

    async def _write_and_commit(self, command: bytes) -> None:
        """Connect, write command + commit frame, disconnect."""
        commit = struct.pack(">BB", 0x3B, 0x00)
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                await self._write_with_retry(client, command)
                await asyncio.sleep(COMMAND_COMMIT_DELAY)
                await self._write_with_retry(client, commit)
                _LOGGER.debug("Command + commit sent successfully")
            except Exception as ex:
                _LOGGER.error(
                    "Write failed (command=%s, commit=%s): %s",
                    command.hex(), commit.hex(), ex,
                )
                raise
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
        _LOGGER.debug(
            "Sprinkle station %d for %d min — payload: %s",
            station, minutes, command.hex(),
        )
        await self._write_and_commit(command)

    async def stop_manual_sprinkle(self) -> None:
        """Stop all manual irrigation."""
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        _LOGGER.debug("Stop manual sprinkle — payload: %s", command.hex())
        await self._write_and_commit(command)

    async def turn_on(self) -> None:
        """Turn on the controller."""
        command = struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)
        _LOGGER.debug("Turn on controller — payload: %s", command.hex())
        await self._write_and_commit(command)

    async def turn_off_permanent(self) -> None:
        """Turn off the controller permanently."""
        command = struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)
        _LOGGER.debug("Turn off controller permanently — payload: %s", command.hex())
        await self._write_and_commit(command)

    async def stop_manual_sprinkle_repeated(self, attempts: int = 3) -> None:
        """Send stop command multiple times within a single connection for reliability."""
        command = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)
        commit = struct.pack(">BB", 0x3B, 0x00)
        _LOGGER.debug(
            "Stop manual sprinkle (repeated %dx) — payload: %s",
            attempts, command.hex(),
        )
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                for attempt in range(1, attempts + 1):
                    _LOGGER.debug("Stop attempt %d/%d", attempt, attempts)
                    await self._write_with_retry(client, command)
                    await asyncio.sleep(COMMAND_COMMIT_DELAY)
                    await self._write_with_retry(client, commit)
                    if attempt < attempts:
                        await asyncio.sleep(1.0)
                _LOGGER.debug("Stop command sent %d times successfully", attempts)
            except Exception as ex:
                _LOGGER.error(
                    "Stop repeated write failed on attempt: %s", ex,
                )
                raise
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

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

    async def _subscribe_send_and_collect(
        self,
        client: BleakClient,
        command: bytes,
        label: str,
    ) -> list[dict]:
        """Subscribe to notify, send command+commit, collect notifications."""
        received: list[dict] = []

        def _on_notification(sender, data: bytearray) -> None:
            hex_data = data.hex()
            _LOGGER.info(
                "  [%s] NOTIFICATION: %s (%d bytes)", label, hex_data, len(data),
            )
            received.append({"data": hex_data, "raw": list(data)})

        commit = struct.pack(">BB", 0x3B, 0x00)

        await client.start_notify(NOTIFY_UUID, _on_notification)
        _LOGGER.info("  [%s] Sending command: %s", label, command.hex())
        await client.write_gatt_char(CHARACTERISTIC_UUID, command, response=True)
        await asyncio.sleep(COMMAND_COMMIT_DELAY)
        await client.write_gatt_char(CHARACTERISTIC_UUID, commit, response=True)
        _LOGGER.info("  [%s] Waiting %.0fs for notifications...", label, NOTIFY_WAIT_SECONDS)
        await asyncio.sleep(NOTIFY_WAIT_SECONDS)

        try:
            await client.stop_notify(NOTIFY_UUID)
        except Exception:  # noqa: BLE001
            pass

        _LOGGER.info("  [%s] Received %d notification(s)", label, len(received))
        return received

    async def diagnose_irrigation_cycle(self) -> dict:
        """Run start→capture→stop→capture cycle to map irrigation state bytes."""
        start_cmd = struct.pack(">HBBBBH", 0x3105, 0x22, 1, 0x00, 1, 0xFFFF)
        stop_cmd = struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)

        async with self._conn_lock:
            client = await self._connect_client()
            try:
                _LOGGER.info("=== IRRIGATION CYCLE DIAGNOSTIC START ===")

                # Phase 1: Start irrigation on station 1 for 1 minute
                start_notifs = await self._subscribe_send_and_collect(
                    client, start_cmd, "START station 1 (1 min)",
                )

                await asyncio.sleep(2.0)

                # Phase 2: Stop irrigation
                stop_notifs = await self._subscribe_send_and_collect(
                    client, stop_cmd, "STOP irrigation",
                )

                _LOGGER.info("=== IRRIGATION CYCLE DIAGNOSTIC END ===")
                return {
                    "start_notifications": start_notifs,
                    "stop_notifications": stop_notifs,
                }
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    async def diagnose_device(self) -> dict:
        """Full GATT diagnostic: discover characteristics, read values, subscribe to 108b0003.

        Subscribes ONLY to the Solem custom notify characteristic (108b0003)
        to avoid the authorization error on 0x2A05 that drops the connection.
        After subscribing, sends a turn-on command to trigger a response.
        """
        async with self._conn_lock:
            client = await self._connect_client()
            try:
                result = {}
                _LOGGER.info("=== SOLEM DEVICE DIAGNOSTIC START ===")
                _LOGGER.info("Device: %s, MTU: %s", self.mac_address, client.mtu_size)

                # Discover all services and characteristics
                for service in client.services:
                    _LOGGER.info("Service: %s", service.uuid)
                    service_chars = []
                    for char in service.characteristics:
                        _LOGGER.info(
                            "  Char: %s  Properties: %s", char.uuid, char.properties,
                        )
                        char_info = {
                            "uuid": char.uuid,
                            "properties": char.properties,
                        }

                        # Try reading characteristics that support it
                        if "read" in char.properties:
                            try:
                                data = await client.read_gatt_char(char.uuid)
                                hex_data = data.hex()
                                _LOGGER.info(
                                    "    READ -> %s (%d bytes)", hex_data, len(data),
                                )
                                char_info["read_value"] = hex_data
                            except Exception as ex:
                                _LOGGER.warning("    READ FAILED: %s", ex)
                                char_info["read_error"] = str(ex)

                        service_chars.append(char_info)
                    result[service.uuid] = service_chars

                # Subscribe ONLY to Solem custom notify characteristic
                notifications_received: list[dict] = []

                def _on_notification(sender, data: bytearray) -> None:
                    hex_data = data.hex()
                    _LOGGER.info(
                        "  NOTIFICATION from %s: %s (%d bytes)",
                        sender, hex_data, len(data),
                    )
                    notifications_received.append(
                        {"sender": str(sender), "data": hex_data}
                    )

                try:
                    await client.start_notify(NOTIFY_UUID, _on_notification)
                    _LOGGER.info("  Subscribed to Solem notify: %s", NOTIFY_UUID)
                except Exception as ex:
                    _LOGGER.error("  Subscribe to %s FAILED: %s", NOTIFY_UUID, ex)
                    result["_notifications"] = []
                    result["_notify_error"] = str(ex)
                    _LOGGER.info("=== SOLEM DEVICE DIAGNOSTIC END (no notify) ===")
                    return result

                # Send turn-on command to provoke a response
                turn_on_cmd = struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)
                commit = struct.pack(">BB", 0x3B, 0x00)
                _LOGGER.info("  Sending turn-on command to trigger response...")
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID, turn_on_cmd, response=True,
                )
                await asyncio.sleep(COMMAND_COMMIT_DELAY)
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID, commit, response=True,
                )
                _LOGGER.info("  Command sent, waiting 5 seconds for notifications...")
                await asyncio.sleep(5.0)

                try:
                    await client.stop_notify(NOTIFY_UUID)
                except Exception:  # noqa: BLE001
                    pass

                result["_notifications"] = notifications_received
                _LOGGER.info(
                    "  Received %d notification(s)", len(notifications_received),
                )
                _LOGGER.info("=== SOLEM DEVICE DIAGNOSTIC END ===")
                return result

            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
