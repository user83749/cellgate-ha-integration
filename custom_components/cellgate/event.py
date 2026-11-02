"""Emit a standard doorbell event when an incoming SIP call rings."""

from homeassistant.components.event import EventEntity, EventDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if coordinator.calls:
        async_add_entities([CellGateCallEvent(coordinator)])


class CellGateCallEvent(EventEntity):
    _attr_has_entity_name = True
    _attr_name = "Incoming call"
    _attr_event_types = ["ring"]
    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(self, coordinator):
        self.manager = coordinator.calls
        self.seen = self.manager.ring_sequence
        self._attr_unique_id = coordinator.client.account_key + "_incoming_call"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.client.account_key + "_intercom")}
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.subscribe(self.changed))

    @callback
    def changed(self):
        if self.manager.ring_sequence > self.seen:
            self.seen = self.manager.ring_sequence
            self._trigger_event("ring")
        self.async_write_ha_state()
