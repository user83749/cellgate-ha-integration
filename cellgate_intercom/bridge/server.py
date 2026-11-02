"""Authenticated HA signaling API and ARI call lifecycle; no media processing."""

from __future__ import annotations
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import hmac
import re
import secrets

import aiohttp
from aiohttp import web
from .config import BackendError, config_value, host_port, resolve_next_hop
from .signaling import Viewer

API_VERSION = 1


@dataclass
class Offer:
    call_id: str
    sdp: str
    candidates: list = field(default_factory=list)
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    viewer: Viewer | None = None


def complete_offer(sdp, candidates, codecs):
    """Keep original ICE/DTLS and encoded video; constrain codec negotiation only."""
    if not isinstance(sdp, str) or len(sdp) > 60000 or not sdp.startswith("v=0"):
        raise BackendError("Invalid WebRTC offer")
    sections = sdp.replace("\r\n", "\n").strip().split("\nm=")
    prepared = []
    mids = {}
    bundle = re.search(r"^a=group:BUNDLE (.+)$", sections[0], re.M)
    bundled_mids = set(bundle[1].split()) if bundle else set()
    for index, section in enumerate(sections[1:]):
        lines = ("m=" + section).splitlines()
        mid = next((line[6:] for line in lines if line.startswith("a=mid:")), None)
        for candidate in candidates:
            if candidate.get("mid") == mid or (
                candidate.get("mid") is None and candidate.get("index") == index
            ):
                value = candidate.get("candidate", "")
                if not isinstance(value, str):
                    continue
                value = value.removeprefix("candidate:")
                if (
                    value
                    and "\n" not in value
                    and "\r" not in value
                    and len(value) <= 2048
                ):
                    line = "a=candidate:" + value
                    if line not in lines:
                        lines.append(line)
        prepared.append((mid, lines))
        mids[mid] = lines

    def ice_credentials(lines):
        return tuple(
            next(
                (
                    line
                    for line in lines + sections[0].splitlines()
                    if line.startswith(prefix)
                ),
                None,
            )
            for prefix in ("a=ice-ufrag:", "a=ice-pwd:")
        )

    output = [sections[0]]
    for mid, lines in prepared:
        # Chrome/max-bundle can trickle candidates only for the shared transport.
        # Reuse them only when both media sections have identical ICE credentials.
        if mid in bundled_mids and not any(
            line.startswith("a=candidate:") for line in lines
        ):
            credentials = ice_credentials(lines)
            if all(credentials):
                for other_mid in bundled_mids:
                    other = mids.get(other_mid, [])
                    if ice_credentials(other) == credentials:
                        shared = [
                            line for line in other if line.startswith("a=candidate:")
                        ]
                        if shared:
                            lines.extend(shared)
                            break
        fields = lines[0].split()
        if fields[0] == "m=video" and fields[1] != "0":
            allowed = {
                line.split(":", 1)[1].split()[0]
                for line in lines
                if line.startswith("a=rtpmap:")
                and line.split()[1].split("/")[0].lower() in codecs
            }
            fields = fields[:3] + [pt for pt in fields[3:] if pt in allowed]
            if len(fields) == 3:
                raise BackendError("No shared intercom and viewer video codec")
            lines[0] = " ".join(fields)
            lines = [
                line
                for line in lines
                if not re.match(r"a=(?:rtpmap|fmtp|rtcp-fb):\d+", line)
                or line.split(":", 1)[1].split()[0] in allowed
            ]
        if (
            not any(line.startswith("a=candidate:") for line in lines)
            and fields[1] != "0"
        ):
            raise BackendError("No ICE candidates received for the viewer")
        if "a=end-of-candidates" not in lines:
            lines.append("a=end-of-candidates")
        output.append("\n".join(lines))
    return "\r\n".join("\n".join(output).splitlines()) + "\r\n"


class Bridge:
    def __init__(self, config, token):
        self.config = config
        self.token = token
        self.client = None
        self.ari_ready = asyncio.Event()
        self.owner = None
        self.account = None
        self.peer = None
        self.call = None
        self.bridge_id = None
        self.sessions = {}
        self.subscribers = set()
        self.tasks = []
        self.registered = False
        self.last_error = None
        self.epoch = secrets.token_hex(8)
        self.ring_sequence = 0
        self.lock = asyncio.Lock()
        self.offer_lock = asyncio.Lock()
        self.stopped = False
        self.disabling = False
        self.next_probe = 0

    def snapshot(self):
        return {
            "api_version": API_VERSION,
            "epoch": self.epoch,
            "owner": self.owner,
            "registered": self.registered,
            "state": "active"
            if self.call and self.call["answered"]
            else "ringing"
            if self.call
            else "idle"
            if self.registered
            else "connecting"
            if self.owner
            else "disabled",
            "call": {"id": self.call["id"], "answered": self.call["answered"]}
            if self.call
            else None,
            "can_talk": bool(self.call and self.call.get("audio")),
            "ring_sequence": self.ring_sequence,
            "last_error": self.last_error,
        }

    async def publish(self):
        state = self.snapshot()
        for ws in tuple(self.subscribers):
            try:
                await asyncio.wait_for(ws.send_json(state), 2)
            except (TimeoutError, ConnectionError, RuntimeError):
                self.subscribers.discard(ws)

    async def ari(self, method, path, **params):
        async with self.client.request(
            method,
            self.config.ari_url + path,
            params=params,
            auth=aiohttp.BasicAuth("cellgate", self.config.ari_password),
            timeout=aiohttp.ClientTimeout(total=8),
        ) as response:
            if response.status == 404 and method == "DELETE":
                return None
            if response.status >= 300:
                raise BackendError("Asterisk call control failed")
            return (
                await response.json()
                if response.content_type == "application/json"
                else None
            )

    async def start(self):
        self.client = aiohttp.ClientSession()
        self.tasks = [
            asyncio.create_task(self.events()),
            asyncio.create_task(self.monitor()),
        ]
        await asyncio.wait_for(self.ari_ready.wait(), 20)

    async def events(self):
        while not self.stopped:
            try:
                async with self.client.ws_connect(
                    self.config.ari_url + "/events",
                    params={"app": "cellgate", "subscribeAll": "false"},
                    auth=aiohttp.BasicAuth("cellgate", self.config.ari_password),
                    heartbeat=20,
                ) as ws:
                    self.ari_ready.set()
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            try:
                                await self.event(message.json())
                            except (BackendError, aiohttp.ClientError, TimeoutError):
                                self.last_error = (
                                    "Unable to handle an Asterisk call event"
                                )
                                await self.publish()
            except (aiohttp.ClientError, TimeoutError):
                pass
            if not self.stopped:
                self.ari_ready.clear()
                self.last_error = "Asterisk event connection lost"
                # Fail closed on control loss; never adopt unknown/orphaned calls.
                await self.end_call()
                await asyncio.sleep(3)

    async def monitor(self):
        while not self.stopped:
            try:
                status = await self.config.cli("pjsip show registrations")
                registered = bool(self.owner and re.search(r"\bRegistered\b", status))
                if registered != self.registered:
                    self.registered = registered
                    await self.publish()
            except (BackendError, TimeoutError):
                self.registered = False
                await self.publish()
            now = asyncio.get_running_loop().time()
            if (
                self.owner
                and not self.registered
                and not self.call
                and now >= self.next_probe
            ):
                self.next_probe = now + 60
                try:
                    async with self.lock:
                        account = self.account
                        if account and not self.call:
                            peer = await resolve_next_hop(account)
                            if peer != self.peer:
                                self.config.write(
                                    "provider.conf", self.config.provider(account, peer)
                                )
                                await self.config.cli(
                                    "module reload res_pjsip.so res_pjsip_authenticator_digest.so res_pjsip_endpoint_identifier_ip.so res_pjsip_outbound_registration.so"
                                )
                                self.peer = peer
                except (BackendError, OSError, TimeoutError):
                    pass
            await asyncio.sleep(5)

    async def event(self, event):
        kind = event.get("type")
        channel = event.get("channel", {})
        channel_id = channel.get("id")
        if kind == "StasisStart":
            args = event.get("args", [])
            if args == ["incoming"]:
                if not self.owner or self.call or self.disabling:
                    await self.ari("DELETE", f"/channels/{channel_id}", reason="busy")
                    return
                video = await self.ari(
                    "GET",
                    f"/channels/{channel_id}/variable",
                    variable="PJSIP_MEDIA_OFFER(video)",
                )
                audio = await self.ari(
                    "GET",
                    f"/channels/{channel_id}/variable",
                    variable="PJSIP_MEDIA_OFFER(audio)",
                )
                self.call = {
                    "id": secrets.token_hex(16),
                    "channel": channel_id,
                    "answered": False,
                    "video": set(
                        re.findall(r"\b(?:h264|vp8)\b", video.get("value", "").lower())
                    ),
                    "audio": bool(audio.get("value")),
                }
                self.ring_sequence += 1
                self.last_error = None
                await self.ari("POST", f"/channels/{channel_id}/ring")
                await self.publish()
                return
            if len(args) == 3 and args[0] == "viewer":
                offer = self.sessions.get(args[2])
                call = self.call
                if (
                    not offer
                    or not call
                    or args[1] != call["id"]
                    or offer.call_id != call["id"]
                    or not offer.viewer
                ):
                    await self.ari("DELETE", f"/channels/{channel_id}")
                    return
                offer.viewer.channel = channel_id
                if self.bridge_id is None:
                    self.bridge_id = secrets.token_hex(16)
                    await self.ari(
                        "POST", f"/bridges/{self.bridge_id}", type="mixing,proxy_media"
                    )
                if not call["answered"]:
                    await self.ari("POST", f"/channels/{call['channel']}/answer")
                    call["answered"] = True
                    await self.ari(
                        "POST",
                        f"/bridges/{self.bridge_id}/addChannel",
                        channel=call["channel"],
                    )
                await self.ari("POST", f"/channels/{channel_id}/answer")
                await self.ari(
                    "POST", f"/bridges/{self.bridge_id}/addChannel", channel=channel_id
                )
                await self.publish()
                return
            await self.ari("DELETE", f"/channels/{channel_id}")
        elif kind in {"StasisEnd", "ChannelDestroyed"}:
            if self.call and self.call["channel"] == channel_id:
                await self.end_call(hangup=False)
            else:
                for sid, offer in list(self.sessions.items()):
                    if offer.viewer and offer.viewer.channel == channel_id:
                        self.sessions.pop(sid, None)
                        await offer.viewer.close()

    async def configure(self, owner, account):
        async with self.lock:
            if self.owner and self.owner != owner:
                raise BackendError(
                    "This backend already belongs to another CellGate account"
                )
            for key in ("username", "password", "host"):
                config_value(account.get(key))
            host_port(account["host"])
            if self.account == account and self.owner == owner:
                return self.snapshot()
            if self.call:
                raise BackendError(
                    "SIP configuration cannot change during an active call"
                )
            peer = await resolve_next_hop(account)
            self.config.write("provider.conf", self.config.provider(account, peer))
            await self.config.cli(
                "module reload res_pjsip.so res_pjsip_authenticator_digest.so res_pjsip_endpoint_identifier_ip.so res_pjsip_outbound_registration.so"
            )
            self.account = dict(account)
            self.owner = owner
            self.peer = peer
            self.next_probe = asyncio.get_running_loop().time() + 60
            self.last_error = None
            await self.publish()
            return self.snapshot()

    async def detach(self, sid):
        offer = self.sessions.pop(sid, None)
        if offer and offer.viewer:
            if offer.viewer.channel:
                with suppress(BackendError, aiohttp.ClientError, TimeoutError):
                    await self.ari("DELETE", f"/channels/{offer.viewer.channel}")
            await offer.viewer.close()

    async def end_call(self, hangup=True):
        call = self.call
        self.call = None
        for sid in list(self.sessions):
            await self.detach(sid)
        if call and hangup:
            with suppress(BackendError, aiohttp.ClientError, TimeoutError):
                await self.ari("DELETE", f"/channels/{call['channel']}")
        if self.bridge_id:
            with suppress(BackendError, aiohttp.ClientError, TimeoutError):
                await self.ari("DELETE", f"/bridges/{self.bridge_id}")
            self.bridge_id = None
        await self.publish()

    async def disable(self, owner):
        async with self.lock:
            if self.owner != owner:
                return
            self.disabling = True
            await self.end_call()
            # Keep the auth object alive while the registrar challenges the
            # Expires: 0 request. Removing it first prevents digest completion.
            with suppress(BackendError, TimeoutError):
                await self.config.cli("pjsip send unregister cellgate-registration")
                async with asyncio.timeout(5):
                    while re.search(
                        r"\bRegistered\b",
                        await self.config.cli("pjsip show registrations"),
                    ):
                        await asyncio.sleep(0.1)
            self.config.write("provider.conf", "")
            await self.config.cli(
                "module reload res_pjsip.so res_pjsip_authenticator_digest.so res_pjsip_endpoint_identifier_ip.so res_pjsip_outbound_registration.so"
            )
            self.owner = None
            self.account = None
            self.peer = None
            self.registered = False
            self.disabling = False
            await self.publish()

    def prepare(self, sid, call_id, sdp):
        if not self.call or call_id != self.call["id"]:
            raise BackendError("The intercom call is no longer available")
        if self.sessions:
            raise BackendError("This intercom call already has a viewer")
        if not isinstance(sdp, str) or len(sdp) > 60000:
            raise BackendError("Invalid WebRTC offer")
        self.sessions[sid] = Offer(call_id, sdp)

    async def offer(self, sid):
        async with self.offer_lock:
            call = self.call
            offer = self.sessions.get(sid)
            if not call or not offer or offer.call_id != call["id"]:
                raise BackendError("The intercom call is no longer available")
            if offer.viewer:
                raise BackendError("This viewing session already started")
            call_id = offer.call_id
            sdp = offer.sdp
            try:
                # Non-trickle SIP: collect the native HA viewer's trickled ICE
                # before submitting its SDP, without creating a second media leg.
                if "a=end-of-candidates" in sdp:
                    offer.complete.set()
                try:
                    await asyncio.wait_for(offer.complete.wait(), 5)
                except TimeoutError:
                    pass
                if self.call is not call or self.sessions.get(sid) is not offer:
                    raise BackendError("The intercom call ended before viewing began")
                sdp = complete_offer(sdp, offer.candidates, call["video"])
                offer.viewer = Viewer(self.client, self.config, call_id, sid)
                answer = await offer.viewer.offer(sdp)
                if self.call is not call:
                    raise BackendError("The intercom call ended before viewing began")
                return answer
            except BaseException:
                await self.detach(sid)
                raise

    async def close(self):
        self.stopped = True
        if self.owner:
            with suppress(Exception):
                await self.disable(self.owner)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for ws in tuple(self.subscribers):
            await ws.close()
        if self.client:
            await self.client.close()


def application(bridge):
    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            ("Bearer " + bridge.token).encode(),
        ):
            raise web.HTTPUnauthorized()
        try:
            return await handler(request)
        except BackendError as err:
            return web.json_response({"error": str(err)}, status=409)
        except (aiohttp.ClientError, TimeoutError):
            return web.json_response(
                {"error": "Asterisk backend unavailable"}, status=503
            )

    app = web.Application(middlewares=[authenticate], client_max_size=70000)

    def owned(request):
        if bridge.owner != request.match_info["owner"]:
            raise web.HTTPNotFound()

    async def health(request):
        return web.json_response(
            {"api_version": API_VERSION, "ready": bridge.ari_ready.is_set()}
        )

    async def configure(request):
        owner = request.match_info["owner"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", owner):
            raise web.HTTPBadRequest()
        account = await request.json()
        if not isinstance(account, dict):
            raise web.HTTPBadRequest()
        return web.json_response(await bridge.configure(owner, account))

    async def disable(request):
        await bridge.disable(request.match_info["owner"])
        return web.json_response({})

    async def state(request):
        owned(request)
        return web.json_response(bridge.snapshot())

    async def events(request):
        owned(request)
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        bridge.subscribers.add(ws)
        try:
            await ws.send_json(bridge.snapshot())
            async for _ in ws:
                pass
        finally:
            bridge.subscribers.discard(ws)
        return ws

    async def prepare(request):
        owned(request)
        sid = request.match_info["sid"]
        if not re.fullmatch("[a-f0-9]{32}", sid):
            raise web.HTTPBadRequest()
        data = await request.json()
        bridge.prepare(sid, data.get("call_id"), data.get("sdp", ""))
        return web.json_response({})

    async def offer(request):
        owned(request)
        return web.json_response(
            {"answer": await bridge.offer(request.match_info["sid"])}
        )

    async def candidate(request):
        owned(request)
        data = await request.json()
        offer = bridge.sessions.get(request.match_info["sid"])
        if offer:
            if not data.get("candidate"):
                offer.complete.set()
            elif len(offer.candidates) < 64:
                offer.candidates.append(data)
        return web.json_response({})

    async def close_viewer(request):
        owned(request)
        await bridge.detach(request.match_info["sid"])
        return web.json_response({})

    app.router.add_get("/v1/health", health)
    app.router.add_put("/v1/accounts/{owner}", configure)
    app.router.add_delete("/v1/accounts/{owner}", disable)
    app.router.add_get("/v1/accounts/{owner}", state)
    app.router.add_get("/v1/accounts/{owner}/events", events)
    app.router.add_put("/v1/accounts/{owner}/sessions/{sid}", prepare)
    app.router.add_post("/v1/accounts/{owner}/sessions/{sid}", offer)
    app.router.add_post("/v1/accounts/{owner}/sessions/{sid}/candidates", candidate)
    app.router.add_delete("/v1/accounts/{owner}/sessions/{sid}", close_viewer)
    return app
