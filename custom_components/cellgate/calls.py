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
    def __init__(self, account_provider, *, stun_server=None):
        self.listeners = set()
        super().__init__(account_provider, self.notify, stun_server=stun_server)
        self.sessions = {}
        self.offer_lock = asyncio.Lock()
        self.on_call_end = self.close_sessions

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
                raise SipError("This intercom call already has a Home Assistant viewer")
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
                    self.sessions.pop(session_id, None)
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
                await self.accept()
                return pc.localDescription.sdp
            except BaseException:
                self.sessions.pop(session_id, None)
                await pc.close()
                raise

    async def candidate(self, session_id, value, mid=None, index=None):
        pc = self.sessions.get(session_id)
        if pc is None:
            raise SipError("WebRTC session no longer exists")
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

    async def close_viewer(self, session_id):
        pc = self.sessions.pop(session_id, None)
        if pc:
            await pc.close()

    async def close_sessions(self):
        peers = list(self.sessions.values())
        self.sessions.clear()
        await asyncio.gather(*(pc.close() for pc in peers), return_exceptions=True)
