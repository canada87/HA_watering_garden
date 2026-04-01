"""Number entities for the Solem BT Controller integration."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .base import SolemBaseEntity
from .const import DOMAIN, MAX_SAFETY_DURATION, MIN_SAFETY_DURATION
from .coordinator import SolemCoordinator


class SolemStationDurationNumber(SolemBaseEntity, RestoreEntity, NumberEntity):
    """Number entity to set the irrigation duration for a station."""

    _attr_native_min_value = MIN_SAFETY_DURATION
    _attr_native_max_value = MAX_SAFETY_DURATION
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: SolemCoordinator, station_number: int) -> None:
        super().__init__(coordinator)
        self._station_number = station_number
        self._attr_name = f"Station {station_number} Duration"
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.mac_address}_station_{station_number}_duration"
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.stations[self._station_number - 1].safety_duration

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.stations[self._station_number - 1].safety_duration = int(value)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                restored = int(float(last_state.state))
                if MIN_SAFETY_DURATION <= restored <= MAX_SAFETY_DURATION:
                    self.coordinator.stations[
                        self._station_number - 1
                    ].safety_duration = restored
            except (ValueError, TypeError):
                pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem number entities."""
    coordinator: SolemCoordinator = entry.runtime_data

    entities = []
    for i in range(1, coordinator.num_stations + 1):
        entities.append(SolemStationDurationNumber(coordinator, i))

    async_add_entities(entities)
