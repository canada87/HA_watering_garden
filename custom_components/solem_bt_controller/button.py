"""Button entities for the Solem BT Controller integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import SolemBaseEntity
from .const import DOMAIN
from .coordinator import SolemCoordinator


class SolemStartButton(SolemBaseEntity, ButtonEntity):
    """Button to start irrigation on a single station."""

    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, coordinator: SolemCoordinator, station_number: int) -> None:
        super().__init__(coordinator)
        self._station_number = station_number
        self._attr_name = f"Start Station {station_number}"
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.mac_address}_start_{station_number}"
        )

    async def async_press(self) -> None:
        await self.coordinator.start_irrigation(self._station_number)


class SolemStopButton(SolemBaseEntity, ButtonEntity):
    """Button to stop all irrigation."""

    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Stop Irrigation"
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mac_address}_stop"

    async def async_press(self) -> None:
        await self.coordinator.stop_irrigation()


class SolemControllerOnButton(SolemBaseEntity, ButtonEntity):
    """Button to turn on the controller."""

    _attr_icon = "mdi:power"

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Turn On"
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mac_address}_turn_on"

    async def async_press(self) -> None:
        await self.coordinator.turn_controller_on()


class SolemControllerOffButton(SolemBaseEntity, ButtonEntity):
    """Button to turn off the controller."""

    _attr_icon = "mdi:power-off"

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Turn Off"
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mac_address}_turn_off"

    async def async_press(self) -> None:
        await self.coordinator.turn_controller_off()


class SolemDiagnoseButton(SolemBaseEntity, ButtonEntity):
    """Button to run full GATT diagnostic — results are logged."""

    _attr_icon = "mdi:stethoscope"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Diagnose Device"
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mac_address}_diagnose"

    async def async_press(self) -> None:
        await self.coordinator.diagnose_device()


class SolemDiagnoseIrrigationButton(SolemBaseEntity, ButtonEntity):
    """Run start→stop cycle to capture state change notifications."""

    _attr_icon = "mdi:test-tube"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Diagnose Irrigation Cycle"
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mac_address}_diagnose_cycle"

    async def async_press(self) -> None:
        await self.coordinator.diagnose_irrigation_cycle()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solem button entities."""
    coordinator: SolemCoordinator = entry.runtime_data

    entities: list[ButtonEntity] = []

    for i in range(1, coordinator.num_stations + 1):
        entities.append(SolemStartButton(coordinator, i))

    entities.append(SolemStopButton(coordinator))
    entities.append(SolemControllerOnButton(coordinator))
    entities.append(SolemControllerOffButton(coordinator))
    entities.append(SolemDiagnoseButton(coordinator))
    entities.append(SolemDiagnoseIrrigationButton(coordinator))

    async_add_entities(entities)
