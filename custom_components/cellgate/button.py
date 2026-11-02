"""Explicit gate commands; no optimistic position assumption."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .api import CellGateAuthError, CellGateError
from .entity import CellGateEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_discovered():
        entities = []
        for port_id, port in coordinator.data.items():
            if port_id in known:
                continue
            if not port.get("momentary_allowed", False):
                continue
            known.add(port_id)
            entities.append(CellGateButton(coordinator, port_id))
        async_add_entities(entities)

    add_discovered()
    entry.async_on_unload(coordinator.async_add_listener(add_discovered))


class CellGateButton(CellGateEntity, ButtonEntity):
    def __init__(self, coordinator, port_id):
        super().__init__(coordinator, port_id, "trigger")
        self._attr_name = f"{self.port.get('name', 'Gate')} Trigger gate"
        self._attr_icon = "mdi:gate"

    async def async_press(self):
        if not self.available:
            raise HomeAssistantError("Gate is unavailable")
        try:
            await self.coordinator.client.command(self.port)
        except CellGateAuthError as err:
            # Refresh the session for the next deliberate press; never replay this command.
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                "CellGate rejected the command. Check account permissions and sign-in."
            ) from err
        except CellGateError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
