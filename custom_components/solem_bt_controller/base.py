"""Base entity for the Solem BT Controller integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolemCoordinator


class SolemBaseEntity(CoordinatorEntity[SolemCoordinator]):
    """Common base for all Solem entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolemCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.mac_address)},
            name=f"Solem {self.coordinator.mac_address[-5:]}",
            manufacturer="Solem",
            model="BL-IP",
        )
