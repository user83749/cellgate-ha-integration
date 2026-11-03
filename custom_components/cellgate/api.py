"""Independent asynchronous implementation of the CellGate mobile protocol.

Gate request contracts are documented by the public reference integration.
Mobile login, discovery, and SIP provisioning were validated with authorized
requests. SIP registration and call media handling are not implemented.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import API_PREFIX, APP_TYPE, APP_VERSION, MOBILE_URL


class CellGateError(Exception):
    """A mobile API communication or protocol failure."""


class CellGateAuthError(CellGateError):
    """The mobile session or credentials were rejected."""


class CellGatePermissionError(CellGateError):
    """The account is not allowed to perform an operation."""


class CellGateRejectedError(CellGateError):
    """CellGate explicitly rejected a request."""


class CellGateCommandUncertain(CellGateError):
    """A command was sent but its acknowledgment was lost or invalid."""


@dataclass(frozen=True, repr=False)
class SipAccount:
    """Private SIP provisioning data. Never expose this as entity attributes."""

    username: str
    password: str
    host: str
    registration_server: str | None
    audio_codec_order: tuple[int, ...]
    audio_codec_order_mobile: tuple[int, ...]
    video_codec_order: tuple[int, ...]
    video_codec_order_mobile: tuple[int, ...]
    dtmf_order: tuple[str, ...]
    provisioning_interval: int | None


def sip_account(value: Any) -> SipAccount:
    """Parse the observed account object without logging its values."""
    if not isinstance(value, dict):
        raise CellGateError("Mobile SIP account is missing")
    required = [value.get(key) for key in ("username", "password", "host")]
    if any(not isinstance(v, str) or not v.strip() for v in required):
        raise CellGateError("Mobile SIP credentials or host are incomplete")

    def codecs(key):
        text = value.get(key)
        if text in (None, ""):
            return ()
        if not isinstance(text, str):
            raise CellGateError("Invalid mobile SIP codec configuration")
        try:
            return tuple(int(part.strip()) for part in text.split(",") if part.strip())
        except ValueError:
            raise CellGateError("Invalid mobile SIP codec configuration") from None

    interval = value.get("extProvInterval")
    try:
        interval = int(interval) if interval not in (None, "") else None
    except (TypeError, ValueError):
        raise CellGateError("Invalid mobile SIP provisioning interval") from None
    registrar = value.get("sipisRegServer")
    registrar = (
        registrar.strip() if isinstance(registrar, str) and registrar.strip() else None
    )
    dtmf = value.get("dtmfOrder")
    if dtmf is not None and not isinstance(dtmf, str):
        raise CellGateError("Invalid mobile SIP DTMF configuration")
    return SipAccount(
        username=required[0],
        password=required[1],
        host=required[2],
        registration_server=registrar,
        audio_codec_order=codecs("codecOrder"),
        audio_codec_order_mobile=codecs("codecOrder3G"),
        video_codec_order=codecs("videoCodecOrder"),
        video_codec_order_mobile=codecs("videoCodecOrder3G"),
        dtmf_order=tuple(
            part.strip() for part in (dtmf or "").split(",") if part.strip()
        ),
        provisioning_interval=interval,
    )


def token_expiry(token: str) -> float | None:
    """Use the unverified JWT exp only as a cache hint, never as authentication."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        value = claims.get("exp")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and value > 0 else None
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def _id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value).strip()
    return result if result else None


def device_locations(locations: Any) -> list[tuple[str, str, str]]:
    """Parse the mobile location tree, including its documented misspelled key."""
    if not isinstance(locations, list):
        raise CellGateError("Unexpected mobile location response")
    result = []
    seen = set()
    for location in locations:
        if not isinstance(location, dict):
            raise CellGateError("Invalid mobile location record")
        devices = location.get("DeviceList", [])
        if not isinstance(devices, list):
            raise CellGateError("Invalid mobile location devices")
        for device in devices:
            if not isinstance(device, dict):
                raise CellGateError("Invalid mobile device record")
            device_id = _id(device.get("DeviceId"))
            property_id = _id(device.get("ProertyLocaitonId")) or _id(
                device.get("PropertyLocationId")
            )
            if device_id is None or property_id is None:
                raise CellGateError("Mobile device identifiers are missing")
            key = (device_id, property_id)
            if key not in seen:
                seen.add(key)
                result.append((*key, str(location.get("Description") or "")))
    return result


def gate_ports(
    device: Any, device_id: str, property_id: str, location_name: str
) -> dict[str, dict[str, Any]]:
    """Expose explicitly identified gates/doors, never camera ports as triggers."""
    if not isinstance(device, dict) or not isinstance(device.get("ports"), list):
        raise CellGateError("Unexpected mobile port response")
    result = {}
    for port in device["ports"]:
        if not isinstance(port, dict):
            raise CellGateError("Invalid mobile port record")
        device_type = port.get("deviceType")
        if not isinstance(device_type, str) or device_type.casefold().strip() not in {
            "gate",
            "door",
        }:
            continue
        port_id = _id(port.get("id"))
        if port_id is None:
            raise CellGateError("Mobile gate port identifier is missing")
        online = port.get("isOnline")
        online = None if online is None else online is True or online in (1, "1")
        result[f"{device_id}_{port_id}"] = {
            "device_id": device_id,
            "port_id": port_id,
            "property_location_id": property_id,
            "location_name": location_name,
            "device_name": str(device.get("description") or "Gate"),
            "name": str(port.get("description") or "Gate"),
            "hardware_type": str(device.get("hardwareType") or ""),
            "device_type": device_type,
            "is_online": online,
            "last_state": port.get("lastState"),
            "state_label": port.get(
                {"0": "descriptionState0", "1": "descriptionState1"}.get(
                    str(port.get("lastState")), ""
                )
            ),
            "momentary_allowed": device.get("supportsMomentary") is not False
            and port.get("hasMomentaryOpenAccessEnabled") is not False,
        }
    return result


class CellGateClient:
    """Mobile-session client with bounded read recovery and no command replay."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self.session = session
        self.username, self.password = username, password
        self.account_key = hashlib.sha256(
            username.strip().casefold().encode()
        ).hexdigest()[:24]
        self._token: str | None = None
        self._expires: float | None = None
        self._auth_rejected = False
        self._sip_account: SipAccount | None = None
        self._auth_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()

    async def _post(
        self,
        endpoint: str,
        *,
        headers: dict[str, str],
        form: dict[str, str] | None = None,
    ) -> dict:
        request_headers = {"Accept": "application/json", **headers}
        if form is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            async with self.session.post(
                MOBILE_URL + API_PREFIX + "/" + endpoint,
                headers=request_headers,
                data=form,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise CellGateAuthError("CellGate rejected the mobile session")
                if response.status == 403:
                    raise CellGatePermissionError("CellGate denied this operation")
                if not 200 <= response.status < 300:
                    raise CellGateError(f"Mobile API returned HTTP {response.status}")
                try:
                    result = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise CellGateError("Invalid mobile API response") from err
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # Never include request headers, URLs with credentials, or server bodies.
            raise CellGateError("Unable to communicate with CellGate") from None
        if not isinstance(result, dict):
            raise CellGateError("Unexpected mobile API response")
        return result

    async def _login_locked(self) -> None:
        try:
            reply = await self._post(
                "Login",
                headers={
                    "AuthUsername": self.username,
                    "AuthPassword": self.password,
                    "DeviceToken": "",
                    "VoipToken": "",
                    "AppVersion": APP_VERSION,
                    "AppType": APP_TYPE,
                },
            )
        except CellGateAuthError:
            self._auth_rejected = True
            raise
        token = reply.get("token")
        if reply.get("isSuccess") is not True:
            self._auth_rejected = True
            raise CellGateAuthError("CellGate rejected mobile sign-in")
        if not isinstance(token, str) or not token:
            raise CellGateError("Mobile sign-in did not return a token")
        self._token, self._expires = token, token_expiry(token)
        self._auth_rejected = False
        self._sip_account = None
        user = reply.get("sipUser")
        if isinstance(user, dict) and user.get("IsSipAppUser") is True:
            details = user.get("ResponseDetails")
            if isinstance(details, dict) and details.get("account") is not None:
                try:
                    self._sip_account = sip_account(details["account"])
                except CellGateError:
                    # SIP provisioning must not prevent gate-only accounts signing in.
                    self._sip_account = None

    def clear_session(self) -> None:
        """Release cached authentication and SIP data on config-entry unload."""
        self._token = None
        self._expires = None
        self._sip_account = None

    async def login(self) -> None:
        """Perform an explicit sign-in during setup or reauthentication."""
        async with self._auth_lock:
            await self._login_locked()

    async def _ensure_session(self) -> str:
        async with self._auth_lock:
            if self._auth_rejected:
                raise CellGateAuthError(
                    "Mobile sign-in was rejected; reauthentication is required"
                )
            if self._token and (
                self._expires is None or time.time() < self._expires - 30
            ):
                return self._token
            await self._login_locked()
            return self._token

    @staticmethod
    def _success(reply: dict) -> dict:
        if reply.get("isSuccess") is not True:
            # Keep CellGate's own reason; it is the only clue why a command failed.
            data = reply.get("data")
            reason = (data.get("result") if isinstance(data, dict) else None) or reply.get(
                "message"
            )
            detail = f": {str(reason).strip()[:200]}" if reason else ""
            raise CellGateRejectedError("CellGate rejected the mobile request" + detail)
        return reply

    async def _read(self, endpoint: str, form: dict[str, str] | None = None) -> dict:
        # These POST operations retrieve data and may be replayed once after 401.
        if endpoint not in {
            "GetMapLocationList",
            "DevicesList",
            "GetSipInformationForUser",
        }:
            raise CellGateError("Unsupported mobile read")
        for attempt in range(2):
            token = await self._ensure_session()
            headers = {"Authorization": f"Bearer {token}"}
            if form is None:
                headers["AppVersion"] = APP_VERSION
            try:
                return self._success(
                    await self._post(endpoint, headers=headers, form=form)
                )
            except CellGateAuthError:
                async with self._auth_lock:
                    if self._token == token:
                        self._token = None
                if attempt:
                    self._auth_rejected = True
                    raise
        raise CellGateError("Unable to read mobile data")

    async def get_sip_account(self, *, refresh: bool = False) -> SipAccount | None:
        """Obtain provisioning data only; this does not register or answer SIP calls."""
        await self._ensure_session()
        if self._sip_account is not None and not refresh:
            return self._sip_account
        reply = await self._read("GetSipInformationForUser")
        data = reply.get("data")
        if not isinstance(data, dict) or "sipInfo" not in data:
            raise CellGateError("Mobile SIP-information response is incomplete")
        info = data["sipInfo"]
        if info is None:
            self._sip_account = None
            return None
        if not isinstance(info, dict):
            raise CellGateError("Invalid mobile SIP-information response")
        self._sip_account = sip_account(info.get("account"))
        return self._sip_account

    async def discover(self) -> dict[str, dict[str, Any]]:
        reply = await self._read("GetMapLocationList")
        data = reply.get("data")
        if not isinstance(data, dict) or "mapLocations" not in data:
            raise CellGateError("Mobile locations are missing")
        result = {}
        for device_id, property_id, location in device_locations(data["mapLocations"]):
            reply = await self._read(
                "DevicesList",
                {"DeviceID": device_id, "PropertyLocationID": property_id},
            )
            data = reply.get("data")
            if not isinstance(data, dict):
                raise CellGateError("Mobile device details are missing")
            result.update(
                gate_ports(data.get("devices"), device_id, property_id, location)
            )
        return result

    async def command(self, port: dict[str, Any]) -> None:
        """Send one momentary gate request. Do not retry after any response error."""
        if str(port.get("device_type", "")).casefold().strip() not in {"gate", "door"}:
            raise CellGatePermissionError("This port is not an identified gate or door")
        if not port.get("momentary_allowed", False):
            raise CellGatePermissionError(
                "Momentary triggering is not allowed for this port"
            )
        device_id, port_id = _id(port.get("device_id")), _id(port.get("port_id"))
        if device_id is None or port_id is None:
            raise CellGateError("Gate identifiers are missing; no command sent")
        async with self._command_lock:
            token = await self._ensure_session()
            try:
                reply = await self._post(
                    "GateCommand",
                    headers={"Authorization": f"Bearer {token}"},
                    form={
                        "GateCommand": "MOMENTARY_OPEN",
                        "Hours": "0",
                        "DeviceID": device_id,
                        "PortID": port_id,
                        "AccessCode": "1",
                    },
                )
            except CellGateAuthError:
                async with self._auth_lock:
                    if self._token == token:
                        self._token = None
                raise
            except CellGatePermissionError:
                raise
            except CellGateError as err:
                raise CellGateCommandUncertain(
                    "Command outcome is unknown. Check the gate before sending another command."
                ) from err
            data = reply.get("data")
            result = data.get("result") if isinstance(data, dict) else None
            # The cloud queues the relay pulse for the cellular gate and answers
            # isSuccess=false with result "Processing"; that is an accepted command.
            if isinstance(result, str) and result.strip().casefold() == "processing":
                return
            self._success(reply)
