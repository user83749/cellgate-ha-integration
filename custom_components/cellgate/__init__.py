"""CellGate cloud integration."""

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CellGateAuthError, CellGateClient, CellGateError
from .const import DOMAIN, CONF_ENABLE_CALLS

PLATFORMS = [Platform.COVER, Platform.SENSOR]
LOGGER = logging.getLogger(__name__)


class CellGateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
            always_update=False,
        )
        self.client = client
        self.calls = None

    async def _async_update_data(self):
        try:
            return await self.client.discover()
        except CellGateAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CellGateError as err:
            raise UpdateFailed(str(err)) from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = CellGateClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = CellGateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    from .intercom import AsteriskCallManager

    # Keep one camera/manager for the config entry, including while calls are
    # disabled. An options toggle must not remove/recreate tokenized HA entities.
    coordinator.calls = AsteriskCallManager(
        async_get_clientsession(hass), client.get_sip_account, client.account_key
    )
    await coordinator.calls.set_options(entry.options)
    entry.async_on_unload(coordinator.calls.stop)

    async def stop_calls(event):
        await coordinator.calls.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_calls)
    )
    entry.runtime_data = coordinator
    # Preserve old registry metadata while retiring the superseded button.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.domain == "button"
            and entity.unique_id.startswith(client.account_key + "_")
            and entity.unique_id.endswith("_trigger")
            and entity.disabled_by is None
        ):
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
    entry.async_on_unload(client.clear_session)
    await hass.config_entries.async_forward_entry_setups(entry, platforms(entry))
    entry.async_on_unload(entry.add_update_listener(update_options))
    if entry.options.get(CONF_ENABLE_CALLS, False):
        await coordinator.calls.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, platforms(entry)):
        return True
    return False


def platforms(entry):
    return PLATFORMS + [Platform.CAMERA, Platform.EVENT]


async def update_options(hass, entry):
    manager = entry.runtime_data.calls
    await manager.set_options(entry.options)
    if entry.options.get(CONF_ENABLE_CALLS, False):
        await manager.start()
    else:
        await manager.stop()
