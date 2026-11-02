"""Mobile-reported raw port state; no inferred physical position."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import CellGateEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_discovered():
        ports = set(coordinator.data) - known
        known.update(ports)
        async_add_entities(
            CellGateStatus(coordinator, port_id, "reported_state") for port_id in ports
        )

    add_discovered()
    entry.async_on_unload(coordinator.async_add_listener(add_discovered))


class CellGateStatus(CellGateEntity, SensorEntity):
    _attr_icon = "mdi:gate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, port_id, suffix):
        super().__init__(coordinator, port_id, suffix)
        self._attr_name = f"{self.port.get('name', 'Gate')} Reported state"

    @property
    def native_value(self):
        label = self.port.get("state_label")
        if isinstance(label, str) and label:
            return label[:255]
        value = self.port.get("last_state")
        return str(value)[:255] if isinstance(value, (str, int, float)) else None

    @property
    def extra_state_attributes(self):
        return {
            "online": self.port.get("is_online"),
            "device_type": self.port.get("device_type"),
        }
