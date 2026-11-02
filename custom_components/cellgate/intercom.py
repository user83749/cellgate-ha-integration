"""Native HA camera signaling to the dedicated Asterisk companion service.

No SIP/media stack runs inside HA. SDP and ICE are forwarded without decoding
video or audio. No Home Assistant HTTP authentication endpoints are accessed.
"""

from __future__ import annotations
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import secrets
from urllib.parse import urlsplit
import aiohttp

from .api import CellGateError, CellGateAuthError
from .const import CONF_BACKEND_URL, CONF_BACKEND_TOKEN


class IntercomError(Exception):
    """Safe backend error for the camera interface."""


class IntercomAuthError(IntercomError):
    """Stop rather than retry rejected backend authentication."""


def backend_url(value):
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port == 8123
        ):
            raise ValueError
        return value.strip().rstrip("/")
    except (ValueError, AttributeError):
        raise IntercomError(
            "Enter the CellGate Intercom backend URL, including its port"
        ) from None


async def check_backend(client, url, token):
    url = backend_url(url)
    try:
        async with client.get(
            url + "/v1/health",
            headers={"Authorization": "Bearer " + token},
            timeout=aiohttp.ClientTimeout(total=8),
            allow_redirects=False,
        ) as response:
            if response.status in (401, 403):
                raise IntercomAuthError("Backend API token rejected")
            if response.status != 200:
                raise IntercomError("Not a compatible CellGate Intercom backend")
            data = await response.json()
            if data.get("api_version") != 1 or data.get("ready") is not True:
                raise IntercomError("CellGate Intercom backend is not ready")
    except (aiohttp.ClientError, TimeoutError):
        raise IntercomError("Unable to connect to CellGate Intercom backend") from None
    return url


@dataclass
class IntercomCall:
    id: str
    answered: bool = False


@dataclass
class Session:
    id: str = field(default_factory=lambda: secrets.token_hex(16))
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class AsteriskCallManager:
    def __init__(self, client, account_provider, owner):
        self.client = client
        self.account_provider = account_provider
        self.owner = owner
        self.listeners = set()
        self.sessions = {}
        self.options = {}
        self.worker = None
        self.call = None
        self.registered = False
        self.state = "disabled"
        self.last_error = None
        self.can_talk = False
        self.ring_sequence = 0
        self.last_ring = None
        self.offer_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.stopped = True
        self.url = None
        self.token = None
        self.auth_blocked = False
        self.configured = False

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def notify(self):
        for listener in tuple(self.listeners):
            listener()

    async def set_options(self, options):
        changed = any(
            self.options.get(key) != options.get(key)
            for key in (CONF_BACKEND_URL, CONF_BACKEND_TOKEN)
        )
        if changed and self.worker:
            await self.stop()
        self.options = dict(options)

    async def request(self, method, path, **kwargs):
        if self.auth_blocked:
            raise IntercomAuthError(
                "Backend authentication is blocked until options are updated"
            )
        try:
            async with self.client.request(
                method,
                self.url + path,
                headers={"Authorization": "Bearer " + self.token},
                timeout=aiohttp.ClientTimeout(total=35),
                allow_redirects=False,
                **kwargs,
            ) as response:
                if response.status in (401, 403):
                    self.auth_blocked = True
                    raise IntercomAuthError(
                        "CellGate Intercom API token rejected; update the integration options"
                    )
                if response.status in (404, 301, 302, 307, 308):
                    raise IntercomAuthError(
                        "Incompatible CellGate Intercom backend URL; update the integration options"
                    )
                if response.status != 200:
                    raise IntercomError(
                        "CellGate Intercom backend could not complete the request"
                    )
                return await response.json()
        except (aiohttp.ClientError, TimeoutError):
            raise IntercomError("Unable to reach CellGate Intercom backend") from None

    @property
    def path(self):
        return "/v1/accounts/" + self.owner

    async def start(self):
        async with self.lifecycle_lock:
            if self.worker:
                return
            try:
                self.url = backend_url(self.options.get(CONF_BACKEND_URL, ""))
                self.token = self.options.get(CONF_BACKEND_TOKEN, "")
                if len(self.token) < 32:
                    raise IntercomError(
                        "Configure the CellGate Intercom backend URL and API token"
                    )
            except IntercomError as err:
                self.last_error = str(err)
                self.state = "configuration_required"
                self.notify()
                return
            self.auth_blocked = False
            self.stopped = False
            self.state = "connecting"
            self.last_error = None
            self.notify()
            self.worker = asyncio.create_task(self.run())

    def update(self, data):
        call = data.get("call")
        if data.get("owner") != self.owner:
            raise IntercomError("CellGate backend account does not match")
        self.call = (
            IntercomCall(call["id"], bool(call.get("answered"))) if call else None
        )
        self.registered = bool(data.get("registered"))
        self.state = data.get("state", "connecting")
        self.can_talk = bool(data.get("can_talk"))
        self.last_error = data.get("last_error")
        ring = (data.get("epoch"), data.get("ring_sequence"))
        if call and not call.get("answered") and ring != self.last_ring:
            self.ring_sequence += 1
            self.last_ring = ring
        if not call:
            for session in self.sessions.values():
                session.ready.set()
            self.sessions.clear()
        self.notify()

    async def run(self):
        delay = 5
        try:
            while not self.stopped:
                try:
                    await check_backend(self.client, self.url, self.token)
                    account = await self.account_provider()
                    if account is None:
                        raise IntercomAuthError(
                            "CellGate account has no SIP provisioning"
                        )
                    self.update(
                        await self.request(
                            "PUT",
                            self.path,
                            json={
                                "username": account.username,
                                "password": account.password,
                                "host": account.host,
                            },
                        )
                    )
                    self.configured = True
                    async with self.client.ws_connect(
                        self.url + self.path + "/events",
                        headers={"Authorization": "Bearer " + self.token},
                        heartbeat=20,
                        timeout=aiohttp.ClientWSTimeout(ws_close=5),
                        max_msg_size=10000,
                    ) as ws:
                        delay = 5
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                self.update(message.json())
                    raise IntercomError("CellGate Intercom connection ended")
                except (IntercomAuthError, CellGateAuthError) as err:
                    self.auth_blocked = True
                    self.registered = False
                    self.call = None
                    self.can_talk = False
                    self.state = "authentication_failed"
                    self.last_error = str(err)
                    self.notify()
                    return
                except (
                    IntercomError,
                    CellGateError,
                    aiohttp.ClientError,
                    TimeoutError,
                    ValueError,
                    KeyError,
                ):
                    self.registered = False
                    self.call = None
                    self.can_talk = False
                    self.state = "reconnecting"
                    self.last_error = "Unable to connect to CellGate Intercom backend"
                    self.notify()
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        async with self.lifecycle_lock:
            self.stopped = True
            if self.worker:
                self.worker.cancel()
                with suppress(asyncio.CancelledError):
                    await self.worker
                self.worker = None
                if (
                    self.url
                    and self.token
                    and self.configured
                    and not self.auth_blocked
                ):
                    with suppress(IntercomError, TimeoutError):
                        await asyncio.wait_for(self.request("DELETE", self.path), 8)
            for session in self.sessions.values():
                session.ready.set()
            self.sessions.clear()
            self.configured = False
            self.call = None
            self.registered = False
            self.can_talk = False
            self.state = "disabled"
            self.last_error = None
            self.notify()

    async def offer(self, sdp, session_id):
        async with self.offer_lock:
            if not self.call:
                raise IntercomError("No incoming intercom call is available")
            if self.sessions:
                raise IntercomError("This intercom call already has a viewer")
            call = self.call
            session = Session()
            self.sessions[session_id] = session
            try:
                await self.request(
                    "PUT",
                    self.path + "/sessions/" + session.id,
                    json={"call_id": call.id, "sdp": sdp},
                )
                session.ready.set()
                if (
                    self.sessions.get(session_id) is not session
                    or not self.call
                    or self.call.id != call.id
                ):
                    raise IntercomError(
                        "The viewing session was closed before it could start"
                    )
                reply = await self.request(
                    "POST", self.path + "/sessions/" + session.id, json={}
                )
                if (
                    not self.call
                    or self.call.id != call.id
                    or self.sessions.get(session_id) is not session
                ):
                    raise IntercomError("The intercom call ended before viewing began")
                return reply["answer"]
            except BaseException:
                session.ready.set()
                if self.sessions.get(session_id) is session:
                    self.sessions.pop(session_id, None)
                with suppress(IntercomError):
                    await self.request("DELETE", self.path + "/sessions/" + session.id)
                raise

    async def candidate(self, session_id, value, mid=None, index=None):
        session = self.sessions.get(session_id)
        if session is None:
            return
        await session.ready.wait()
        if self.sessions.get(session_id) is not session:
            return
        await self.request(
            "POST",
            self.path + "/sessions/" + session.id + "/candidates",
            json={"candidate": value, "mid": mid, "index": index},
        )

    async def close_viewer(self, session_id):
        session = self.sessions.pop(session_id, None)
        if session:
            session.ready.set()
            with suppress(IntercomError):
                await self.request("DELETE", self.path + "/sessions/" + session.id)
