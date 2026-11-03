"""Local SIP WebSocket signaling only; Asterisk owns provider SIP and all media."""

from __future__ import annotations

import asyncio
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass

import aiohttp

from .auth import challenge_fields, digest
from .config import BackendError

SipError = BackendError


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


class Viewer:
    def __init__(self, client, config, call_id, session_id):
        self.client = client
        self.config = config
        self.call_id = call_id
        self.session_id = session_id
        self.ws = None
        self.reader = None
        self.channel = None
        self.closed = False
        self.responses = asyncio.Queue()
        self.tag = secrets.token_hex(12)
        self.sip_id = secrets.token_hex(16)
        self.to = "<sip:view@127.0.0.1>"
        self.uri = "sip:view@127.0.0.1"
        self.seq = 1
        self.request = None
        self.branch = "z9hG4bK" + secrets.token_hex(12)

    def packet(self, method, body="", auth=""):
        lines = [
            f"{method} {self.uri} SIP/2.0",
            f"Via: SIP/2.0/WS 127.0.0.1;branch={self.branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:ha-viewer@127.0.0.1>;tag={self.tag}",
            f"To: {self.to}",
            f"Call-ID: {self.sip_id}",
            f"CSeq: {self.seq} {method}",
            "Contact: <sip:ha-viewer@127.0.0.1;transport=ws>",
            f"X-CellGate-Call: {self.call_id}",
            f"X-CellGate-Session: {self.session_id}",
        ]
        if auth:
            lines.append(auth)
        if body:
            lines.append("Content-Type: application/sdp")
        return wire(lines, body).decode()

    async def receive(self):
        try:
            async for message in self.ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                packet = Message.parse(message.data.encode())
                if packet.get("call-id") != self.sip_id:
                    continue
                if packet.status:
                    self.responses.put_nowait(packet)
                elif packet.method in {"BYE", "OPTIONS", "INFO"}:
                    # Re-INVITE/session-timer processing stays in Asterisk. This
                    # local endpoint does not renegotiate during a viewing leg.
                    code = 200 if packet.method != "INFO" else 415
                    lines = (
                        [
                            f"SIP/2.0 {code} "
                            + ("OK" if code == 200 else "Unsupported Media Type")
                        ]
                        + [f"Via: {v}" for v in packet.headers["via"]]
                        + [
                            f"From: {packet.get('from')}",
                            f"To: {packet.get('to')}",
                            f"Call-ID: {self.sip_id}",
                            f"CSeq: {packet.get('cseq')}",
                        ]
                    )
                    await self.ws.send_str(wire(lines).decode())
                    if packet.method == "BYE":
                        self.closed = True
                elif packet.method == "INVITE":
                    lines = (
                        ["SIP/2.0 488 Not Acceptable Here"]
                        + [f"Via: {v}" for v in packet.headers["via"]]
                        + [
                            f"From: {packet.get('from')}",
                            f"To: {packet.get('to')}",
                            f"Call-ID: {self.sip_id}",
                            f"CSeq: {packet.get('cseq')}",
                        ]
                    )
                    await self.ws.send_str(wire(lines).decode())
        finally:
            self.closed = True
            self.responses.put_nowait(None)

    async def offer(self, sdp):
        self.ws = await self.client.ws_connect(
            self.config.ws_url, protocols=["sip"], max_msg_size=65535
        )
        if self.closed:
            await self.ws.close()
            raise BackendError("The viewing session was closed")
        self.reader = asyncio.create_task(self.receive())
        auth = ""
        for attempt in range(2):
            self.request = self.packet("INVITE", sdp, auth)
            await self.ws.send_str(self.request)
            async with asyncio.timeout(20):
                while True:
                    reply = await self.responses.get()
                    if reply is None:
                        raise BackendError("Asterisk viewer connection ended")
                    if reply.get("cseq") != f"{self.seq} INVITE" or reply.status < 200:
                        continue
                    break
            self.to = reply.get("to")
            if reply.status == 200:
                self.uri = address_uri(reply.get("contact"))
                self.branch = "z9hG4bK" + secrets.token_hex(12)
                await self.ws.send_str(self.packet("ACK"))
                return reply.body
            # Non-2xx ACK uses the INVITE transaction branch.
            await self.ws.send_str(self.packet("ACK"))
            if reply.status in (401, 407) and not attempt:
                header = (
                    "www-authenticate" if reply.status == 401 else "proxy-authenticate"
                )
                value = digest(
                    "ha-viewer",
                    self.config.sip_password,
                    self.uri,
                    challenge_fields(reply.get(header)),
                    method="INVITE",
                )
                auth = (
                    "Authorization: "
                    if reply.status == 401
                    else "Proxy-Authorization: "
                ) + value
                self.seq += 1
                self.to = "<sip:view@127.0.0.1>"
                self.branch = "z9hG4bK" + secrets.token_hex(12)
                continue
            raise BackendError(
                f"Asterisk rejected the viewing session (SIP {reply.status})",
                "viewer_sip_rejected",
            )
        raise BackendError("Unable to authenticate the local viewer")

    async def close(self):
        if self.ws and not self.ws.closed:
            if self.channel is None and self.request:
                # A pending INVITE must not turn into an orphaned answered leg.
                with suppress(Exception):
                    await self.ws.send_str(self.packet("CANCEL"))
            await self.ws.close()
        if self.reader:
            self.reader.cancel()
            with suppress(asyncio.CancelledError):
                await self.reader
        self.closed = True
