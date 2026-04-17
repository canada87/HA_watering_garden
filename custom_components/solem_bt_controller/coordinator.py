"""DataUpdateCoordinator for Solem BT Controller."""

import asyncio
import logging

from homeassistant.components.bluetooth import async_last_service_info
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import APIConnectionError, SolemBleApi
from .const import (
    CONF_BLUETOOTH_TIMEOUT,
    CONF_CONTROLLER_MAC,
    CONF_NUM_STATIONS,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_SAFETY_DURATION,
    DOMAIN,
)
from .models import IrrigationController, IrrigationStation

_LOGGER = logging.getLogger(__name__)


class SolemCoordinator(DataUpdateCoordinator):
    """Manages state and BLE commands for the Solem controller."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)

        self.mac_address: str = entry.data[CONF_CONTROLLER_MAC]
        self.num_stations: int = entry.data[CONF_NUM_STATIONS]
        bt_timeout = entry.options.get(CONF_BLUETOOTH_TIMEOUT, DEFAULT_BLUETOOTH_TIMEOUT)

        self.api = SolemBleApi(self.mac_address, bt_timeout)

        # Models
        self.controller = IrrigationController(self.mac_address)
        self.stations: list[IrrigationStation] = []
        for i in range(1, self.num_stations + 1):
            key = f"station_{i}_safety_duration"
            duration = entry.options.get(
                key, entry.data.get(key, DEFAULT_SAFETY_DURATION)
            )
            self.stations.append(IrrigationStation(i, duration))

        # Irrigation tracking
        self._irrigation_stop_event = asyncio.Event()
        self._active_station: int | None = None
        self._irrigation_task: asyncio.Task | None = None
        self._last_rssi: int | None = None

    async def _async_update_data(self) -> dict:
        """No polling — state is managed optimistically."""
        return {}

    @property
    def last_rssi(self) -> int | None:
        """Return the last known RSSI value from HA bluetooth service info."""
        return self._last_rssi

    def _update_rssi(self) -> None:
        """Read RSSI from HA bluetooth integration (works with ESPHome proxy)."""
        try:
            service_info = async_last_service_info(
                self.hass, self.mac_address, connectable=True
            )
            if service_info is not None and service_info.rssi != 0:
                self._last_rssi = service_info.rssi
                _LOGGER.debug("RSSI updated: %d dBm", self._last_rssi)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Could not read RSSI: %s", ex)

    def _apply_device_state(self, state: dict) -> None:
        """Update models from parsed BLE notification state."""
        self._update_rssi()
        battery = state.get("battery_level")
        if battery is not None:
            self.controller.update_battery(battery)
            _LOGGER.debug("Battery level updated: %d%%", battery)

        if not state.get("raw_packets"):
            # Device sent no notifications — do not interpret silence as "not irrigating".
            # The caller is responsible for applying optimistic state if needed.
            _LOGGER.debug("No BLE notifications received — skipping station state update")
            return

        # Update irrigation state from device response
        is_irrigating = state.get("is_irrigating", False)
        active_station = state.get("active_station")

        if is_irrigating and active_station is not None:
            station_found = any(s.station_number == active_station for s in self.stations)
            if station_found:
                _LOGGER.debug(
                    "Device reports irrigation active on station %d", active_station,
                )
                for s in self.stations:
                    s.update_state("Sprinkling" if s.station_number == active_station else "Stopped")
            else:
                _LOGGER.debug(
                    "Device reports irrigation active (station %d out of range — ignoring)",
                    active_station,
                )
        elif is_irrigating and active_station is None:
            _LOGGER.debug("Device reports irrigation active (station unknown)")
        elif not is_irrigating and active_station is None:
            _LOGGER.debug("Device reports no active irrigation")
            for s in self.stations:
                s.update_state("Stopped")
            self._active_station = None

    # -- Irrigation control --

    async def start_irrigation(self, station_number: int) -> None:
        """Send BLE start command and begin monitoring the irrigation."""
        # If another station is running, signal it to stop tracking
        if self._irrigation_task and not self._irrigation_task.done():
            self._irrigation_stop_event.set()
            # Give the old monitor task a moment to exit
            await asyncio.sleep(0.1)

        station = self.stations[station_number - 1]

        try:
            state = await self.api.sprinkle_station(
                station_number, station.safety_duration,
            )
            self._apply_device_state(state)
        except APIConnectionError as ex:
            _LOGGER.error("Failed to start station %d: %s", station_number, ex)
            return

        # If device state didn't set irrigation, either abort or apply optimistic fallback.
        if station.state != "Sprinkling":
            if state.get("is_irrigating") is False and state.get("raw_packets") and not state.get("session_active"):
                # Device responded with confirmed permanently-off state (session_id=0x000000,
                # frame_type=0x02). The BLE Turn On command does NOT undo this; only the app
                # or physical button can reactivate the controller.
                _LOGGER.warning(
                    "Station %d: device confirmed irrigation did NOT start (session inactive). "
                    "Turn the controller ON from the app before starting irrigation.",
                    station_number,
                )
                self.async_set_updated_data({})
                return
            # No notifications received, or device replied with an unrecognized active-session
            # frame type (e.g. 0x62 under weak RSSI) — apply optimistic state.
            _LOGGER.debug(
                "Station %d: no confirmed BLE state — applying optimistic Sprinkling state",
                station_number,
            )
            for s in self.stations:
                s.update_state("Stopped")
            station.update_state("Sprinkling")

        self._active_station = station_number
        self._irrigation_stop_event = asyncio.Event()
        self.async_set_updated_data({})

        # Background task: wait for stop signal or safety timeout
        self._irrigation_task = self.hass.async_create_task(
            self._monitor_irrigation(station_number, station.safety_duration)
        )

    async def _monitor_irrigation(
        self, station_number: int, duration_minutes: int
    ) -> None:
        """Wait for stop event or safety timeout, then mark station as stopped."""
        try:
            await asyncio.wait_for(
                self._irrigation_stop_event.wait(),
                timeout=duration_minutes * 60,
            )
        except asyncio.TimeoutError:
            _LOGGER.info(
                "Safety timeout reached for station %d (%d min) — sending BLE stop",
                station_number,
                duration_minutes,
            )
            try:
                await self.api.stop_irrigation()
            except APIConnectionError as ex:
                _LOGGER.error(
                    "Safety stop BLE command failed for station %d: %s",
                    station_number,
                    ex,
                )

        # Only update if this station is still the active one
        if self._active_station == station_number:
            self.stations[station_number - 1].update_state("Stopped")
            self._active_station = None
            self.async_set_updated_data({})

    async def stop_irrigation(self) -> None:
        """Send BLE stop command with repeated writes for reliability.

        Uses repeated writes within a single connection (3x) to improve
        reliability on weak BLE signal. Updates state even on BLE failure.
        """
        try:
            state = await self.api.stop_irrigation()
            self._apply_device_state(state)
        except APIConnectionError as ex:
            _LOGGER.error("Failed to stop irrigation: %s", ex)
            # Notify the user — the device may still be irrigating
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Solem BT Controller",
                    "message": (
                        f"Failed to send stop command to {self.mac_address}. "
                        f"The device may still be irrigating. Error: {ex}"
                    ),
                    "notification_id": f"solem_stop_failed_{self.mac_address}",
                },
            )

        # Signal the monitor task to stop
        self._irrigation_stop_event.set()

        # If device state didn't clear irrigation (e.g. no notifications received),
        # fall back to optimistic state
        if any(s.state == "Sprinkling" for s in self.stations):
            for station in self.stations:
                station.update_state("Stopped")
        self._active_station = None
        self.async_set_updated_data({})

    # -- Controller on/off --

    async def turn_controller_on(self) -> None:
        """Send BLE turn-on command."""
        try:
            state = await self.api.turn_on()
            self._apply_device_state(state)
        except APIConnectionError as ex:
            _LOGGER.error("Failed to turn on controller: %s", ex)
            return

        self.controller.update_state("On")
        self.async_set_updated_data({})

    async def turn_controller_off(self) -> None:
        """Send BLE turn-off command."""
        try:
            state = await self.api.turn_off_permanent()
            self._apply_device_state(state)
        except APIConnectionError as ex:
            _LOGGER.error("Failed to turn off controller: %s", ex)
            return

        self.controller.update_state("Off")
        self.async_set_updated_data({})

    # -- State refresh --

    async def refresh_state(self) -> None:
        """Read device state by sending a turn-on command (safe if already on)."""
        try:
            state = await self.api.read_state()
            self._apply_device_state(state)
        except APIConnectionError as ex:
            _LOGGER.error("Failed to refresh state: %s", ex)
            return

        self.controller.update_state("On")
        self.async_set_updated_data({})
