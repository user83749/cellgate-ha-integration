"""A reported-state gate cover with only the supported momentary open action."""

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .api import CellGateAuthError, CellGateError
from .entity import CellGateEntity

OPENING_SECONDS = 15


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
            entities.append(CellGateCover(coordinator, port_id))
        async_add_entities(entities)

    add_discovered()
    entry.async_on_unload(coordinator.async_add_listener(add_discovered))


class CellGateCover(CellGateEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.GATE
    _attr_supported_features = CoverEntityFeature.OPEN
    # State is the relay's reported label, not a position sensor. Without this
    # HA greys out Open whenever the label reads "open".
    _attr_assumed_state = True

    def __init__(self, coordinator, port_id):
        super().__init__(coordinator, port_id, "gate")
        self._attr_name = self.port.get("name", "Gate")
        self._attr_icon = "mdi:gate"

    @property
    def is_closed(self):
        label = str(self.port.get("state_label") or "").strip().lower()
        if label in {"closed", "gate closed", "door closed"}:
            return True
        if label in {"open", "gate open", "door open"}:
            return False
        return None

    @property
    def extra_state_attributes(self):
        return {
            "reported_state": self.port.get("state_label"),
            "last_state": self.port.get("last_state"),
            "online": self.port.get("is_online"),
        }

    async def async_open_cover(self, **kwargs):
        # A second momentary pulse can stop or reverse the gate; HomeKit and the
        # frontend can send Open twice for one tap.
        if self._attr_is_opening:
            return
        self._attr_is_opening = True
        self.async_write_ha_state()
        try:
            await self.coordinator.client.command(self.port)
        except CellGateAuthError as err:
            self._finish_opening()
            # Refresh the session for the next deliberate press; never replay this command.
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                "CellGate rejected the command. Check account permissions and sign-in."
            ) from err
        except CellGateError as err:
            self._finish_opening()
            raise HomeAssistantError(str(err)) from err
        # Show "opening" while the cloud delivers the pulse, then re-read state.
        self.async_on_remove(
            async_call_later(self.hass, OPENING_SECONDS, self._finish_opening)
        )

    @callback
    def _finish_opening(self, _now=None):
        if self._attr_is_opening:
            self._attr_is_opening = False
            self.async_write_ha_state()
            self.hass.async_create_task(self.coordinator.async_request_refresh())
