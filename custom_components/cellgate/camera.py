"""Native Home Assistant WebRTC camera for an incoming intercom call."""

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

try:
    # Added in Core 2026.11; gate/video support also works on 2026.10.
    from homeassistant.components.camera import CameraEntityCapabilities
except ImportError:
    CameraEntityCapabilities = None

from .const import DOMAIN
from .sip import SipError
from .sip_sdp import MediaError


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if coordinator.calls:
        async_add_entities([CellGateCamera(coordinator)])


class CellGateCamera(Camera):
    _attr_has_entity_name = True
    _attr_name = "Intercom"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "CellGate"
    _attr_model = "SIP intercom"

    def __init__(self, coordinator):
        super().__init__()
        self.manager = coordinator.calls
        self._attr_unique_id = coordinator.client.account_key + "_intercom"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.client.account_key + "_intercom")},
            manufacturer="CellGate",
            name="CellGate intercom",
            model="SIP intercom",
        )

    @property
    def available(self):
        return self.manager.call is not None and (
            self.manager.registered or self.manager.call.answered
        )

    @property
    def extra_state_attributes(self):
        return {"call_state": self.manager.state, "last_error": self.manager.last_error}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.subscribe(self.changed))
        self.changed()

    @callback
    def changed(self):
        self._attr_is_streaming = self.manager.state == "active"
        if CameraEntityCapabilities:
            self._attr_camera_entity_capabilities = CameraEntityCapabilities(
                two_way_audio=self.manager.can_talk
            )
        self.async_write_ha_state()

    async def async_camera_image(self, width=None, height=None):
        return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp, session_id, send_message
    ):
        try:
            answer = await self.manager.offer(offer_sdp, session_id)
            send_message(WebRTCAnswer(answer))
        except (SipError, MediaError) as err:
            send_message(WebRTCError("cellgate_call_unavailable", str(err)))
        except Exception:
            # aiortc/SDP errors can include remote descriptions; never expose them.
            send_message(
                WebRTCError(
                    "cellgate_media_error",
                    "Unable to establish the intercom media session",
                )
            )

    async def async_on_webrtc_candidate(self, session_id, candidate):
        await self.manager.candidate(
            session_id,
            candidate.candidate,
            candidate.sdp_mid,
            candidate.sdp_m_line_index,
        )

    @callback
    def close_webrtc_session(self, session_id):
        self.hass.async_create_task(self.manager.close_viewer(session_id))
