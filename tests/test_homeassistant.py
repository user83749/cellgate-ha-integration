"""Exercise real Home Assistant lifecycle and entities with synthetic API data."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cellgate.api import CellGateAuthError, CellGateError

ACCOUNT = {CONF_USERNAME: "synthetic@example.test", CONF_PASSWORD: "synthetic-only"}
GATES = {
    "42_7": {
        "device_id": "42",
        "port_id": "7",
        "property_id": "12",
        "name": "Entry",
        "device_name": "Synthetic EVO",
        "hardware_type": "WTM-EVO",
        "device_type": "Gate",
        "is_online": True,
        "last_state": "0",
        "state_label": "Closed",
        "momentary_allowed": True,
    }
}


async def setup(hass):
    entry = MockConfigEntry(
        domain="cellgate", data=ACCOUNT, unique_id="synthetic-account"
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.cellgate.api.CellGateClient.discover",
        AsyncMock(return_value=GATES),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_entities_trigger_and_unload(hass):
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert len(hass.states.async_entity_ids("camera")) == 1
    assert (
        hass.states.get(hass.states.async_entity_ids("camera")[0]).state
        == "unavailable"
    )
    covers = hass.states.async_entity_ids("cover")
    assert len(covers) == 1
    assert not hass.states.async_entity_ids("sensor")
    assert hass.states.get(covers[0]).state == "closed"
    coordinator = entry.runtime_data
    client = coordinator.client
    with (
        patch.object(client, "command", AsyncMock()) as command,
        patch.object(client, "discover", AsyncMock(return_value=GATES)),
    ):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": covers[0]}, blocking=True
        )
        command.assert_awaited_once_with(GATES["42_7"])
    client._token = "synthetic-cached-token"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert client._token is None
    assert client._sip_account is None
    # Home Assistant preserves registered entities as unavailable after unload.
    assert hass.states.get(covers[0]).state == "unavailable"
    assert not coordinator._listeners


async def test_cloud_failure_marks_entities_unavailable(hass):
    entry = await setup(hass)
    with patch.object(
        entry.runtime_data.client,
        "discover",
        AsyncMock(side_effect=CellGateError("Unavailable")),
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    for entity_id in hass.states.async_entity_ids():
        if entity_id.startswith(("cover.", "sensor.")):
            assert hass.states.get(entity_id).state == "unavailable"


async def test_ui_config_flow_and_duplicate(hass):
    with (
        patch("custom_components.cellgate.api.CellGateClient.login", AsyncMock()),
        patch(
            "custom_components.cellgate.api.CellGateClient.discover",
            AsyncMock(return_value=GATES),
        ),
        patch(
            "custom_components.cellgate.async_setup_entry", AsyncMock(return_value=True)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            "cellgate", context={"source": "user"}, data=ACCOUNT
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == ACCOUNT
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_init(
            "cellgate", context={"source": "user"}, data=ACCOUNT
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_bad_credentials_config_flow(hass):
    with patch(
        "custom_components.cellgate.api.CellGateClient.login",
        AsyncMock(side_effect=CellGateAuthError("Rejected")),
    ):
        result = await hass.config_entries.flow.async_init(
            "cellgate", context={"source": "user"}, data=ACCOUNT
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}


async def test_setup_auth_failure_starts_reauth(hass):
    entry = MockConfigEntry(
        domain="cellgate", data=ACCOUNT, unique_id="synthetic-account"
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.cellgate.api.CellGateClient.discover",
        AsyncMock(side_effect=CellGateAuthError("Rejected")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_cover_reports_open_unknown_and_open_only(hass):
    from copy import deepcopy
    from homeassistant.components.cover import CoverEntityFeature

    entry = await setup(hass)
    entity_id = hass.states.async_entity_ids("cover")[0]
    assert not hass.states.async_entity_ids("button")
    assert (
        hass.states.get(entity_id).attributes["supported_features"]
        == CoverEntityFeature.OPEN
    )
    data = deepcopy(GATES)
    data["42_7"]["state_label"] = "Gate Open"
    entry.runtime_data.async_set_updated_data(data)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "open"
    data = deepcopy(data)
    data["42_7"]["state_label"] = "Unspecified"
    data["42_7"]["last_state"] = "0"
    entry.runtime_data.async_set_updated_data(data)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unknown"


async def test_optional_persistent_camera_event_and_unload(hass):
    from types import SimpleNamespace
    from homeassistant.components.camera import DATA_COMPONENT

    entry = MockConfigEntry(
        domain="cellgate",
        data=ACCOUNT,
        unique_id="synthetic-account",
        options={"enable_calls": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.cellgate.api.CellGateClient.discover",
            AsyncMock(return_value=GATES),
        ),
        patch("custom_components.cellgate.calls.CallManager.start", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    camera_id = hass.states.async_entity_ids("camera")[0]
    event_id = hass.states.async_entity_ids("event")[0]
    camera = hass.data[DATA_COMPONENT].get_entity(camera_id)
    manager = entry.runtime_data.calls
    assert hass.states.get(camera_id).state == "unavailable"
    for sequence in (1, 2):
        manager.registered = True
        manager.call = SimpleNamespace(
            answered=False, bridge=SimpleNamespace(can_talk=True)
        )
        manager.ring_sequence = sequence
        manager.set_state("ringing")
        assert hass.states.get(camera_id).state != "unavailable"
        assert hass.states.get(event_id).attributes["event_type"] == "ring"
        assert hass.data[DATA_COMPONENT].get_entity(camera_id) is camera
        assert await camera.async_camera_image() is None
        if hasattr(camera, "camera_entity_capabilities"):
            assert camera.camera_entity_capabilities.two_way_audio is True
        manager.call = None
        manager.set_state("idle")
        assert hass.states.get(camera_id).state == "unavailable"
        if hasattr(camera, "camera_entity_capabilities"):
            assert camera.camera_entity_capabilities.two_way_audio is False
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert manager.closed and not manager.listeners


async def test_options_toggle_preserves_camera_and_manager(hass):
    from homeassistant.components.camera import DATA_COMPONENT

    entry = MockConfigEntry(
        domain="cellgate",
        data=ACCOUNT,
        unique_id="synthetic-account",
        options={"enable_calls": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.cellgate.api.CellGateClient.discover",
            AsyncMock(return_value=GATES),
        ),
        patch("custom_components.cellgate.calls.CallManager.start", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        old = entry.runtime_data.calls
        camera_id = hass.states.async_entity_ids("camera")[0]
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"enable_calls": False}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert old.closed and entry.runtime_data.calls is old
        assert hass.data[DATA_COMPONENT].get_entity(camera_id) is not None
        assert hass.states.get(camera_id).state == "unavailable"


async def test_upgrade_retires_only_legacy_trigger_button(hass):
    from homeassistant.helpers import entity_registry as er
    from custom_components.cellgate.api import CellGateClient

    entry = MockConfigEntry(
        domain="cellgate", data=ACCOUNT, unique_id="synthetic-account"
    )
    entry.add_to_hass(hass)
    account_key = CellGateClient(
        None, ACCOUNT[CONF_USERNAME], ACCOUNT[CONF_PASSWORD]
    ).account_key
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "button", "cellgate", account_key + "_42_7_trigger", config_entry=entry
    )
    with patch(
        "custom_components.cellgate.api.CellGateClient.discover",
        AsyncMock(return_value=GATES),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert (
        registry.async_get(old.entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.async_entity_ids("cover")
    assert not hass.states.async_entity_ids("button")
