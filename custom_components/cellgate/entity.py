"""Shared entity for a mobile-API gate port."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class CellGateEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, port_id, suffix):
        super().__init__(coordinator)
        self.port_id = port_id
        self._attr_unique_id = f"{coordinator.client.account_key}_{port_id}_{suffix}"
        port = self.port
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{coordinator.client.account_key}_{port['device_id']}")
            },
            manufacturer="CellGate",
            model=port.get("hardware_type") or None,
            name=port.get("device_name") or "Gate",
        )

    @property
    def port(self):
        return self.coordinator.data.get(self.port_id, {})

    @property
    def available(self):
        return (
            super().available
            and bool(self.port)
            and self.port.get("is_online") is not False
        )
