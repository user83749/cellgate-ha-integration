"""Asynchronous bounded SIP/UDP registrar client and single incoming dialog.

Supported profile: digest REGISTER, one redirect, INVITE/ACK/CANCEL/BYE/OPTIONS,
RTP media selected by media.py. No outgoing calls, gate commands or push tokens.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
import ipaddress
import re
import secrets
import socket
import time
from urllib.parse import quote

from .api import CellGateError, CellGateAuthError
from .sip_auth import SipAuthError, challenge_fields, digest, safe
from .sip_sdp import MediaError, parse_offer
from .media import MediaBridge


class SipError(Exception):
    """Sanitized signaling error."""


@dataclass(repr=False)
class Message:
    start: str
    headers: dict[str, list[str]]
    body: str

    def get(self, key, default=""):
        return self.headers.get(key, [default])[0]

    @property
    def method(self):
        return self.start.split(" ", 1)[0] if not self.start.startswith("SIP/") else ""

    @property
    def status(self):
        return (
            int(self.start.split(" ", 2)[1])
            if self.start.startswith("SIP/2.0 ")
            else None
        )

    @property
    def branch(self):
        match = re.search(r"(?:^|;)\s*branch=([^;\s]+)", self.get("via"))
        return match.group(1) if match else ""

    @classmethod
    def parse(cls, packet):
        if len(packet) > 65535:
            raise SipError("SIP packet too large")
        try:
            head, body = packet.split(b"\r\n\r\n", 1)
            remainder = head.replace(b"\r\n", b"")
            if b"\r" in remainder or b"\n" in remainder:
                raise ValueError
            lines = head.decode("utf-8").split("\r\n")
            if not re.fullmatch(
                r"(?:SIP/2\.0 [1-6][0-9]{2}(?: .*)?|[A-Z]+ sip:[^\s]+ SIP/2\.0)",
                lines[0],
            ):
                raise ValueError
            headers = {}
            last = None
            for line in lines[1:]:
                if line[:1] in (" ", "\t") and last:
                    headers[last][-1] += " " + line.strip()
                    continue
                key, value = line.split(":", 1)
                key = key.lower().strip()
                key = {
                    "v": "via",
                    "i": "call-id",
                    "f": "from",
                    "t": "to",
                    "m": "contact",
                    "l": "content-length",
                    "c": "content-type",
                }.get(key, key)
                headers.setdefault(key, []).append(value.strip())
                last = key
            for key in ("call-id", "cseq", "from", "to"):
                if len(headers.get(key, [])) != 1:
                    raise ValueError
            if not headers.get("via") or len(headers.get("content-length", [])) > 1:
                raise ValueError
            seq = headers["cseq"][0].split()
            if len(seq) != 2 or not seq[0].isdigit() or not 1 <= int(seq[0]) < 2**31:
                raise ValueError
            if not lines[0].startswith("SIP/") and seq[1] != lines[0].split()[0]:
                raise ValueError
            length = int(headers.get("content-length", [str(len(body))])[0])
            if length != len(body) or length > 60000 or "\x00" in head.decode():
                raise ValueError
            return cls(lines[0], headers, body.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SipError("Malformed SIP message") from None


def wire(lines, body=""):
    raw = body.encode("utf-8")
    return ("\r\n".join(lines + [f"Content-Length: {len(raw)}", "", ""])).encode() + raw


def dialog_tag(value):
    match = re.search(r";tag=([^;>\s]+)", value)
    return match.group(1) if match else ""


def address_uri(value):
    match = re.search(r"<?(sip:[^>\s]+)>?", value)
    if not match:
        raise SipError("SIP address missing")
    return match.group(1)


def server_address(host):
    host = safe(host)
    if host.startswith("sip:"):
        host = host[4:]
    match = re.fullmatch(r"([A-Za-z0-9.-]+)(?::(\d+))?", host)
    if not match or not 1 <= int(match.group(2) or 5060) <= 65535:
        raise SipError("Unsupported SIP server address")
    return match.group(1), int(match.group(2) or 5060)


@dataclass(repr=False)
class IncomingCall:
    request: Message
    peer: tuple[str, int]
    tag: str
    bridge: MediaBridge
    answered: bool = False
    acknowledged: bool = False
    answer_packet: bytes | None = None
    timer: asyncio.Task | None = None


class SipReceiver(asyncio.DatagramProtocol):
    """Own a config entry's SIP transport; state callbacks contain no secrets."""

    def __init__(self, account_provider, changed=lambda: None, *, stun_server=None):
        self.account_provider = account_provider
        self.changed = changed
        self.stun_server = stun_server
        self.transport = None
        self.local_ip = ""
        self.peer = None
        self.trusted_ips = set()
        self.account = None
        self.registered = False
        self.state = "disabled"
        self.last_error = None
        self.call = None
        self.on_call_end = None
        self.ring_sequence = 0
        self.pending = {}
        self.cache = OrderedDict()
        self.tasks = set()
        self.worker = None
        self.closed = False
        self.auth = {}
        self.auth_counts = {}
        self.reg_id = secrets.token_hex(20)
        self.reg_tag = secrets.token_hex(10)
        self.seq = 0
        self.contact_user = "ha-" + secrets.token_hex(12)
        self.contact = None
        self.uri = None
        self.dialog_seq = secrets.randbelow(20000) + 1000
        self.call_lock = asyncio.Lock()
        self.stop_lock = asyncio.Lock()

    def set_state(self, state, error=None):
        self.state = state
        self.last_error = error
        self.changed()

    def connection_made(self, transport):
        self.transport = transport

    def error_received(self, error):
        # UDP errors do not imply that a gate command or call succeeded.
        pass

    def datagram_received(self, packet, addr):
        if addr[0] not in self.trusted_ips or self.closed:
            return
        try:
            message = Message.parse(packet)
            if message.status:
                key = (message.branch, message.get("call-id"), message.get("cseq"))
                pending = self.pending.get(key)
                if (
                    pending
                    and addr == pending[1]
                    and message.status >= 200
                    and not pending[0].done()
                ):
                    pending[0].set_result(message)
                return
            if len(self.tasks) < 8:
                self.spawn(self.handle_request(message, addr))
        except (SipError, ValueError):
            return

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        def complete(t):
            if not t.cancelled() and t.exception():
                self.last_error = "SIP request handling failed"
                self.changed()

        task.add_done_callback(complete)
        return task

    async def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self.registration_loop())

    async def open_transport(self):
        host, port = server_address(self.account.host)
        loop = asyncio.get_running_loop()
        resolved = await loop.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
        self.peer = resolved[0][4][:2]
        self.trusted_ips = {self.peer[0]}
        # Determine a routable local address without transmitting any packet.
        temp, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=self.peer
        )
        self.local_ip = temp.get_extra_info("sockname")[0]
        temp.close()
        await loop.create_datagram_endpoint(lambda: self, local_addr=(self.local_ip, 0))
        local = self.transport.get_extra_info("sockname")
        self.contact = f"<sip:{self.contact_user}@{local[0]}:{local[1]};transport=udp>"
        self.uri = "sip:" + self.account.host.removeprefix("sip:")
        self.auth = {}
        self.auth_counts = {}

    async def registration_loop(self):
        delay = 5
        try:
            while not self.closed:
                try:
                    self.account = await self.account_provider()
                    if self.account is None:
                        self.set_state(
                            "unsupported", "Account has no SIP configuration"
                        )
                        return
                    if self.transport is None:
                        self.set_state("connecting")
                        await self.open_transport()
                    expiry = await self.register(180)
                    self.registered = True
                    delay = 5
                    self.set_state(
                        "ringing"
                        if self.call and not self.call.answered
                        else "active"
                        if self.call
                        else "idle"
                    )
                    await asyncio.sleep(max(5, min(expiry * 0.7, 25)))
                except (CellGateAuthError, SipAuthError):
                    self.registered = False
                    self.set_state(
                        "authentication_failed",
                        "SIP or mobile authentication rejected; reload after correcting credentials",
                    )
                    return
                except (OSError, TimeoutError, SipError, CellGateError, ValueError):
                    self.registered = False
                    self.set_state(
                        "reconnecting", "Unable to register with the SIP server"
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
        except asyncio.CancelledError:
            return
        except Exception:
            self.registered = False
            self.set_state("error", "SIP receiver stopped unexpectedly")

    async def transaction(self, method, uri, headers, body="", peer=None):
        if not self.transport:
            raise SipError("SIP transport unavailable")
        peer = peer or self.peer
        branch = "z9hG4bK" + secrets.token_hex(12)
        local = self.transport.get_extra_info("sockname")
        headers = [
            f"Via: SIP/2.0/UDP {self.local_ip}:{local[1]};rport;branch={branch}",
            "Max-Forwards: 70",
        ] + headers
        packet = wire([f"{method} {uri} SIP/2.0"] + headers, body)
        parsed = Message.parse(packet)
        key = (branch, parsed.get("call-id"), parsed.get("cseq"))
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = (future, peer)
        delay = 0.5
        try:
            for _ in range(5):
                self.transport.sendto(packet, peer)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), delay)
                except TimeoutError:
                    delay = min(delay * 2, 4)
            raise SipError("SIP transaction timed out")
        finally:
            self.pending.pop(key, None)
            if not future.done():
                future.cancel()

    async def register(self, expires):
        redirected = False
        for _ in range(5):
            self.seq += 1
            identity = f"<sip:{quote(self.account.username, safe='-_.!~*\x27()+')}@{self.account.host.removeprefix('sip:')}>"
            headers = [
                f"From: {identity};tag={self.reg_tag}",
                f"To: {identity}",
                f"Call-ID: {self.reg_id}",
                f"CSeq: {self.seq} REGISTER",
                f"Contact: {self.contact};expires={expires}",
                f"Expires: {expires}",
                "User-Agent: CellGate-HA/0.3",
            ]
            for name, fields in self.auth.items():
                self.auth_counts[name] = self.auth_counts.get(name, 0) + 1
                headers.append(
                    name
                    + ": "
                    + digest(
                        self.account.username,
                        self.account.password,
                        self.uri,
                        fields,
                        self.auth_counts[name],
                    )
                )
            response = await self.transaction("REGISTER", self.uri, headers)
            if response.status == 302:
                if redirected or self.auth:
                    raise SipError("Unsupported repeated or authenticated SIP redirect")
                value = response.get("contact")
                match = re.fullmatch(r"<?sip:([0-9.]+):(\d+)>?", value)
                if (
                    not match
                    or not ipaddress.ip_address(match.group(1)).is_global
                    or not 1 <= int(match.group(2)) <= 65535
                ):
                    raise SipError("Unsupported SIP redirect target")
                self.peer = (match.group(1), int(match.group(2)))
                self.trusted_ips.add(self.peer[0])
                redirected = True
                continue
            if response.status in (401, 407):
                name, key = (
                    ("Authorization", "www-authenticate")
                    if response.status == 401
                    else ("Proxy-Authorization", "proxy-authenticate")
                )
                fields = challenge_fields(response.get(key))
                # A fresh nonce is normal on later REGISTER renewal; the same
                # challenge twice in one exchange means credentials failed.
                if (
                    name in self.auth
                    and self.auth[name].get("nonce") == fields.get("nonce")
                    and fields.get("stale", "").lower() != "true"
                ):
                    raise SipAuthError("SIP credentials rejected")
                self.auth[name] = fields
                self.auth_counts[name] = 0
                continue
            if response.status == 403:
                raise SipAuthError("SIP registration denied")
            if (
                expires
                and response.status == 423
                and response.get("min-expires").isdigit()
            ):
                expires = max(expires, min(int(response.get("min-expires")), 3600))
                continue
            if response.status != 200:
                raise SipError("SIP registration was not accepted")
            # Via rport/received describes this UDP mapping. Preserve only a
            # validated address when advertising our unique contact on renewal.
            via = response.get("via")
            received = re.search(r";received=([0-9.]+)(?:;|$)", via)
            rport = re.search(r";rport=(\d+)(?:;|$)", via)
            if received and rport:
                try:
                    address = ipaddress.IPv4Address(received.group(1))
                    port = int(rport.group(1))
                    if address.is_global and 1 <= port <= 65535:
                        self.contact = (
                            f"<sip:{self.contact_user}@{address}:{port};transport=udp>"
                        )
                except ValueError:
                    pass
            effective = response.get("expires")
            for contact in response.headers.get("contact", []):
                if self.contact_user + "@" in contact:
                    if match := re.search(r";expires=(\d+)", contact):
                        effective = match.group(1)
            if expires == 0:
                return 0
            return max(10, int(effective)) if effective.isdigit() else expires
        raise SipError("SIP registration authentication did not complete")

    def response(self, request, peer, code, reason, *, tag=None, body="", extra=None):
        to = request.get("to")
        if tag and ";tag=" not in to:
            to += ";tag=" + tag
        lines = (
            [f"SIP/2.0 {code} {reason}"]
            + [f"Via: {v}" for v in request.headers["via"]]
            + [
                f"From: {request.get('from')}",
                f"To: {to}",
                f"Call-ID: {request.get('call-id')}",
                f"CSeq: {request.get('cseq')}",
            ]
        )
        if body:
            lines += ["Content-Type: application/sdp"]
        lines += extra or []
        packet = wire(lines, body)
        self.transport.sendto(packet, peer)
        key = (request.branch, request.get("call-id"), request.get("cseq"), peer)
        self.cache[key] = (time.monotonic(), packet)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)
        return packet

    async def handle_request(self, request, peer):
        key = (request.branch, request.get("call-id"), request.get("cseq"), peer)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 32 and request.method != "ACK":
            self.transport.sendto(cached[1], peer)
            return
        async with self.call_lock:
            method = request.method
            call = self.call
            matches = (
                call
                and request.get("call-id") == call.request.get("call-id")
                and dialog_tag(request.get("from"))
                == dialog_tag(call.request.get("from"))
            )
            if method == "OPTIONS":
                self.response(
                    request,
                    peer,
                    200,
                    "OK",
                    extra=["Allow: INVITE, ACK, CANCEL, BYE, OPTIONS"],
                )
                return
            if method == "INVITE":
                if call:
                    self.response(
                        request,
                        peer,
                        488 if matches else 486,
                        "Not Acceptable Here" if matches else "Busy Here",
                    )
                    return
                if not self.registered:
                    self.response(request, peer, 480, "Temporarily Unavailable")
                    return
                self.response(request, peer, 100, "Trying")
                try:
                    if (
                        request.get("content-type").split(";")[0].lower()
                        != "application/sdp"
                    ):
                        raise MediaError("SIP call did not include an SDP offer")
                    bridge = MediaBridge(parse_offer(request.body))
                except MediaError as err:
                    self.last_error = str(err)
                    self.changed()
                    self.response(request, peer, 488, "Not Acceptable Here")
                    return
                call = IncomingCall(request, peer, secrets.token_hex(10), bridge)
                self.call = call
                self.ring_sequence += 1
                self.response(request, peer, 180, "Ringing", tag=call.tag)
                call.timer = self.spawn(self.ring_timeout(call))
                self.set_state("ringing")
                return
            if method == "ACK":
                if (
                    matches
                    and call.answered
                    and dialog_tag(request.get("to")) == call.tag
                    and request.get("cseq").split()[0]
                    == call.request.get("cseq").split()[0]
                ):
                    call.acknowledged = True
                    self.set_state("active")
                return
            if (
                method == "CANCEL"
                and matches
                and request.branch == call.request.branch
                and request.get("cseq").split()[0]
                == call.request.get("cseq").split()[0]
            ):
                self.response(request, peer, 200, "OK")
                if not call.answered:
                    self.response(
                        call.request, call.peer, 487, "Request Terminated", tag=call.tag
                    )
                    await self.finish(call)
                return
            if (
                method == "BYE"
                and matches
                and dialog_tag(request.get("to")) == call.tag
            ):
                self.response(request, peer, 200, "OK", tag=call.tag)
                await self.finish(call)
                return
            if method != "ACK":
                self.response(request, peer, 481, "Call/Transaction Does Not Exist")

    async def ring_timeout(self, call):
        await asyncio.sleep(60)
        async with self.call_lock:
            if self.call is call and not call.answered:
                self.response(
                    call.request,
                    call.peer,
                    480,
                    "Temporarily Unavailable",
                    tag=call.tag,
                )
                await self.finish(call)

    async def accept(self):
        async with self.call_lock:
            call = self.call
            if call is None:
                raise SipError("No incoming intercom call")
            if call.answered:
                return call.bridge
            try:
                sdp = await call.bridge.prepare(self.local_ip, self.stun_server)
            except BaseException:
                self.response(
                    call.request, call.peer, 488, "Not Acceptable Here", tag=call.tag
                )
                await self.finish(call)
                raise
            if call.timer:
                call.timer.cancel()
            call.answered = True
            call.bridge.start()
            call.answer_packet = self.response(
                call.request,
                call.peer,
                200,
                "OK",
                tag=call.tag,
                body=sdp,
                extra=[f"Contact: {self.contact}"]
                + [
                    f"Record-Route: {v}"
                    for v in call.request.headers.get("record-route", [])
                ],
            )
            call.timer = self.spawn(self.confirm_answer(call))
            self.set_state("connecting")
            return call.bridge

    async def confirm_answer(self, call):
        delay = 0.5
        for _ in range(10):
            await asyncio.sleep(delay)
            if self.call is not call:
                return
            if call.acknowledged:
                # CellGate owns the accepted call's duration. Viewer closure
                # does not end it, and no arbitrary active-call timer is applied.
                return
            self.transport.sendto(call.answer_packet, call.peer)
            delay = min(delay * 2, 4)
        if self.call is call:
            await self.hangup()

    async def hangup(self):
        async with self.call_lock:
            call = self.call
            if call is None:
                return
            if call.answered:
                try:
                    uri = address_uri(call.request.get("contact"))
                    self.dialog_seq += 1
                    headers = [
                        f"From: {call.request.get('to')};tag={call.tag}",
                        f"To: {call.request.get('from')}",
                        f"Call-ID: {call.request.get('call-id')}",
                        f"CSeq: {self.dialog_seq} BYE",
                    ] + [
                        f"Route: {v}"
                        for v in call.request.headers.get("record-route", [])
                    ]
                    await self.transaction("BYE", uri, headers, peer=call.peer)
                except (SipError, OSError):
                    pass
            else:
                self.response(call.request, call.peer, 486, "Busy Here", tag=call.tag)
            await self.finish(call)

    async def finish(self, call):
        if self.call is not call:
            return
        self.call = None
        if call.timer and call.timer is not asyncio.current_task():
            call.timer.cancel()
        await call.bridge.close()
        if self.on_call_end:
            await self.on_call_end()
        self.set_state("idle" if self.registered else "reconnecting")

    async def stop(self):
        async with self.stop_lock:
            if self.closed:
                return
            await self._stop()

    async def _stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        await self.hangup()
        if self.transport:
            if self.registered:
                with suppress(SipError, SipAuthError, OSError, TimeoutError):
                    await self.register(0)
            self.transport.close()
            self.transport = None
        self.closed = True
        self.registered = False
        tasks = [t for t in self.tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.account = None
        self.auth.clear()
        self.auth_counts.clear()
        self.cache.clear()
        self.set_state("disabled")
