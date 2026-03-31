"""Sensor entities for the Solem BT Controller integration."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import SolemBaseEntity
from .const import DOMAIN
from .coordinator import SolemCoordinator


class SolemStationStateSensor(SolemBaseEntity, SensorEntity):
    """Shows the state of a single station (Sprinkling / Stopped)."""

    def __init__(self, coordinator: SolemCoordinator, station_number: int) -> None:
        super().__init__(coordinator)
        self._station_number = station_number
        self._attr_name = f"Station {station_number}"
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.mac_address}_station_{station_number}_state"
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.stations[self._station_number - 1].state

    @property
    def icon(self) -> str:
        if self.native_value == "Sprinkling":
            return "mdi:sprinkler-variant"
        return "mdi:sprinkler"


class SolemControllerStateSensor(SolemBaseEntity, SensorEntity):
    """Shows the state of the controller (On / Off)."""

    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Controller State"
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.mac_address}_controller_state"
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.controller.state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem sensor entities."""
    coordinator: SolemCoordinator = entry.runtime_data

    entities: list[SensorEntity] = []

    for i in range(1, coordinator.num_stations + 1):
        entities.append(SolemStationStateSensor(coordinator, i))

    entities.append(SolemControllerStateSensor(coordinator))

    async_add_entities(entities)
