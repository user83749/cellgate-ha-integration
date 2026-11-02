"""Downloadable diagnostics: raw mobile-API structure and intercom state, redacted."""

from homeassistant.components.diagnostics import async_redact_data

from .api import CellGateError

REDACT = {
    "username",
    "password",
    "cloud_username",
    "cloud_password",
    "token",
    "AuthUsername",
    "AuthPassword",
    "email",
    "Email",
    "phone",
    "Phone",
    "extProvUrl",
    "backend_token",
}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    client = coordinator.client
    result = {
        "options": async_redact_data(dict(entry.options), REDACT),
        "ports": coordinator.data,
    }
    try:
        result["sip_information"] = async_redact_data(
            await client._read("GetSipInformationForUser"), REDACT
        )
        locations = await client._read("GetMapLocationList")
        result["locations"] = async_redact_data(locations, REDACT)
        devices = []
        for location in (locations.get("data") or {}).get("mapLocations") or []:
            for device in location.get("DeviceList") or []:
                reply = await client._read(
                    "DevicesList",
                    {
                        "DeviceID": str(device.get("DeviceId")),
                        "PropertyLocationID": str(
                            device.get("ProertyLocaitonId")
                            or device.get("PropertyLocationId")
                        ),
                    },
                )
                devices.append(async_redact_data(reply, REDACT))
        result["devices"] = devices
    except CellGateError as err:
        result["mobile_api_error"] = str(err)
    manager = coordinator.calls
    if manager:
        result["intercom"] = {
            "state": manager.state,
            "registered": manager.registered,
            "call": bool(manager.call),
            "can_talk": manager.can_talk,
            "last_error": manager.last_error,
        }
    return result
