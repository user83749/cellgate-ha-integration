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
    assert not hass.states.async_entity_ids("camera")
    buttons = hass.states.async_entity_ids("button")
    assert len(buttons) == 1
    assert len(hass.states.async_entity_ids("sensor")) == 1
    assert hass.states.get(hass.states.async_entity_ids("sensor")[0]).state == "Closed"
    coordinator = entry.runtime_data
    client = coordinator.client
    with (
        patch.object(client, "command", AsyncMock()) as command,
        patch.object(client, "discover", AsyncMock(return_value=GATES)),
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": buttons[0]}, blocking=True
        )
        command.assert_awaited_once_with(GATES["42_7"])
    client._token = "synthetic-cached-token"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert client._token is None
    assert client._sip_account is None
    # Home Assistant preserves registered entities as unavailable after unload.
    assert hass.states.get(buttons[0]).state == "unavailable"
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
        if entity_id.startswith(("button.", "sensor.")):
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
