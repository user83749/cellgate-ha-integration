"""Real local HTTP/WS regression tests with HA's IP-ban middleware enabled."""

from unittest.mock import AsyncMock, patch
from ipaddress import ip_address

from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.components.camera import DATA_COMPONENT
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_LOGIN_THRESHOLD,
    KEY_FAILED_LOGIN_ATTEMPTS,
)

from test_homeassistant import ACCOUNT, GATES


async def load_camera(hass, tmp_path):
    entry = MockConfigEntry(
        domain="cellgate", data=ACCOUNT, unique_id="synthetic-account"
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.cellgate.api.CellGateClient.discover",
            AsyncMock(return_value=GATES),
        ),
        patch("custom_components.cellgate.intercom.AsteriskCallManager.start", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    hass.http.app[KEY_BAN_MANAGER].path = str(tmp_path / "ip_bans.yaml")
    camera_id = hass.states.async_entity_ids("camera")[0]
    return entry, hass.data[DATA_COMPONENT].get_entity(camera_id)


async def test_legacy_missing_camera_url_can_trigger_core_ban(
    hass, hass_client_no_auth, tmp_path
):
    """Reproduce the log's failure path without any password/login request."""
    entry, camera = await load_camera(hass, tmp_path)
    # This is the URL advertised by the old inherited Camera.entity_picture.
    legacy_url = (
        f"/api/camera_proxy/{camera.entity_id}?token={camera.access_tokens[-1]}"
    )
    app = hass.http.app
    app[KEY_LOGIN_THRESHOLD] = 1
    client = await hass_client_no_auth()
    assert await hass.config_entries.async_unload(entry.entry_id)
    response = await client.get(legacy_url)
    assert response.status == 401
    assert ip_address("127.0.0.1") in app[KEY_BAN_MANAGER].ip_bans_lookup


async def test_camera_options_do_not_expose_stale_proxy_urls_or_trip_ban(
    hass, hass_client_no_auth, tmp_path
):
    entry, camera = await load_camera(hass, tmp_path)
    entity_id = camera.entity_id
    app = hass.http.app
    app[KEY_LOGIN_THRESHOLD] = 1
    client = await hass_client_no_auth()
    tokens = tuple(camera.access_tokens)
    manager = entry.runtime_data.calls
    with (
        patch.object(manager, "start", AsyncMock()),
        patch(
            "custom_components.cellgate.api.CellGateClient.discover",
            AsyncMock(return_value=GATES),
        ),
    ):
        for enabled in (True, False, True, False):
            hass.config_entries.async_update_entry(
                entry, options={"enable_calls": enabled}
            )
            await hass.async_block_till_done()
            assert hass.data[DATA_COMPONENT].get_entity(entity_id) is camera
            assert tuple(camera.access_tokens) == tokens
            assert camera.entity_picture is None
            assert "entity_picture" not in hass.states.get(entity_id).attributes
            # Even an old valid thumbnail request still resolves to the same
            # entity and fails as unavailable image, not invalid authentication.
            result = await client.get(
                f"/api/camera_proxy/{entity_id}?token={tokens[-1]}"
            )
            assert result.status not in (401, 403)
    assert not app[KEY_BAN_MANAGER].ip_bans_lookup
    assert not any(app[KEY_FAILED_LOGIN_ATTEMPTS].values())


async def test_ice_after_rejected_offer_or_closed_session_is_quiet(
    hass, hass_ws_client, caplog, tmp_path
):
    entry, camera = await load_camera(hass, tmp_path)
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "camera/webrtc/offer",
            "entity_id": camera.entity_id,
            "offer": "synthetic unused offer",
        }
    )
    assert (await client.receive_json())["success"] is True
    assert (await client.receive_json())["event"]["type"] == "session"
    assert (await client.receive_json())["event"]["type"] == "error"
    for identifier in range(2, 7):
        await client.send_json(
            {
                "id": identifier,
                "type": "camera/webrtc/candidate",
                "entity_id": camera.entity_id,
                "session_id": "ended-session",
                "candidate": {
                    "candidate": "candidate:1 1 udp 1 127.0.0.1 12345 typ host",
                    "sdpMid": "0",
                    "sdpMLineIndex": 0,
                },
            }
        )
        assert (await client.receive_json())["success"] is True
    assert "WebRTC session no longer exists" not in caplog.text
    assert "Unknown error" not in caplog.text
