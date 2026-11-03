"""Own SIP calls and one native WebRTC viewing/talk session per account."""

from __future__ import annotations

import asyncio

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.sdp import candidate_from_sdp

from .sip import SipError, SipReceiver
from .sip_sdp import MediaError


class CallManager(SipReceiver):
    def __init__(self, account_provider, *, stun_server=None, diagnostic=None):
        self.listeners = set()
        super().__init__(account_provider, self.notify, stun_server=stun_server, diagnostic=diagnostic)
        self.sessions = {}
        self.offer_lock = asyncio.Lock()
        self.on_call_end = self.close_sessions

    async def accept(self):
        call = self.call
        was_answered = bool(call and call.answered)
        bridge = await super().accept()
        if call and not was_answered:
            self.spawn(self.monitor_media(call, self.ring_sequence))
        return bridge

    async def monitor_media(self, call, sequence):
        seen = set()
        for _ in range(15):
            await asyncio.sleep(1)
            if self.call is not call:
                return
            for index, channel in enumerate(call.bridge.channels):
                if channel.packets_received and index not in seen:
                    seen.add(index)
                    self.diagnostic(f"Call {sequence}: first incoming {channel.spec.codec.mimeType} RTP packets received")
            receiving = {i for i, c in enumerate(call.bridge.channels) if c.spec.receive}
            if receiving <= seen:
                return
        missing = ", ".join(c.spec.codec.mimeType for i, c in enumerate(call.bridge.channels)
                            if c.spec.receive and i not in seen)
        self.diagnostic(f"Call {sequence}: no incoming RTP after 15 seconds for {missing}")

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def notify(self):
        for listener in tuple(self.listeners):
            listener()

    @property
    def can_talk(self):
        return bool(self.call and self.call.bridge.can_talk)

    async def offer(self, sdp, session_id):
        async with self.offer_lock:
            if not self.call:
                raise SipError("No incoming intercom call is available")
            if self.sessions:
                raise SipError("This intercom call already has a Scrypted viewer")
            if len(sdp) > 60000:
                raise MediaError("WebRTC offer is too large")
            call = self.call
            ice = (
                [RTCIceServer(urls=f"stun:{self.stun_server[0]}:{self.stun_server[1]}")]
                if self.stun_server
                else []
            )
            pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))
            self.sessions[session_id] = pc

            @pc.on("track")
            def track_received(track):
                if track.kind == "audio":
                    for channel in call.bridge.channels:
                        if channel.spec.transmit:
                            channel.spawn(channel.feed_microphone(track))
                            break

            @pc.on("connectionstatechange")
            async def connection_changed():
                if (
                    pc.connectionState in {"failed", "closed"}
                    and self.sessions.get(session_id) is pc
                ):
                    self.diagnostic(f"WebRTC viewer connection: {pc.connectionState}")
                    self.sessions.pop(session_id, None)
                    self.set_talk(False)
                    self.notify()
                    await pc.close()

            try:
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=sdp, type="offer")
                )
                for channel in call.bridge.channels:
                    if channel.spec.receive:
                        pc.addTrack(
                            call.bridge.relay.subscribe(channel.track, buffered=False)
                        )
                for transceiver in pc.getTransceivers():
                    channel = next(
                        (
                            c
                            for c in call.bridge.channels
                            if c.track.kind == transceiver.kind
                        ),
                        None,
                    )
                    if channel:
                        transceiver.direction = (
                            "sendrecv"
                            if channel.spec.receive and channel.spec.transmit
                            else "sendonly"
                            if channel.spec.receive
                            else "recvonly"
                        )
                    else:
                        transceiver.direction = "inactive"
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                if self.call is not call:
                    raise SipError(
                        "The intercom call ended before it could be answered"
                    )
                self.diagnostic(f"Call {self.ring_sequence}: WebRTC offer ready; answering SIP call")
                await self.accept()
                return pc.localDescription.sdp
            except BaseException:
                self.sessions.pop(session_id, None)
                await pc.close()
                raise

    async def candidate(self, session_id, value, mid=None, index=None):
        pc = self.sessions.get(session_id)
        if pc is None or pc.connectionState == "closed":
            # Browsers continue trickling candidates after an unavailable
            # offer, cancellation or closed viewer. These are normal no-ops.
            return
        if not value:
            await pc.addIceCandidate(None)
            return
        try:
            candidate = candidate_from_sdp(value.removeprefix("candidate:"))
            candidate.sdpMid = mid
            candidate.sdpMLineIndex = index
            await pc.addIceCandidate(candidate)
        except (ValueError, AssertionError):
            raise MediaError("Invalid WebRTC ICE candidate") from None

    def set_talk(self, enabled):
        if self.call:
            for channel in self.call.bridge.channels:
                channel.set_talk(enabled)

    async def close_viewer(self, session_id):
        pc = self.sessions.pop(session_id, None)
        if pc:
            self.diagnostic("WebRTC viewer detached; SIP call remains until CellGate ends it")
            self.set_talk(False)
            await pc.close()

    async def close_sessions(self):
        self.set_talk(False)
        peers = list(self.sessions.values())
        self.sessions.clear()
        await asyncio.gather(*(pc.close() for pc in peers), return_exceptions=True)
