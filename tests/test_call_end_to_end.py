"""A local simulated gate sends real SIP, RTP, video/audio and receives talk.

No CellGate service or physical gate is contacted. No STUN server is used.
"""

import array
import asyncio
from fractions import Fraction
import math
from types import SimpleNamespace

import av
from aiortc import (
    AudioStreamTrack,
    RTCPeerConnection,
    RTCConfiguration,
    RTCSessionDescription,
)
from aiortc.codecs import get_encoder, get_decoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from aiortc.rtp import RtpPacket
import pytest
from aioice.ice import get_host_addresses

from custom_components.cellgate.calls import CallManager
from custom_components.cellgate.sip import Message, wire
from custom_components.cellgate.sip_auth import challenge_fields, digest

REAL_IPV4 = get_host_addresses(use_ipv4=True, use_ipv6=False)
REAL_ADDRESSES = get_host_addresses(use_ipv4=True, use_ipv6=True)


class Port(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        self.messages.put_nowait((data, peer))

    @property
    def address(self):
        return self.transport.get_extra_info("sockname")[:2]


class Gate(Port):
    def __init__(self, ip):
        super().__init__()
        self.ip = ip
        self.receiver = None
        self.registrations = []
        self.challenged = False
        self.account = SimpleNamespace(
            username="synthetic-user", password="synthetic-password", host=""
        )
        self.call_id = "synthetic-call"
        self.branch = "z9hG4bK-synthetic-invite"
        self.local_tag = "gate-tag"

    def datagram_received(self, data, peer):
        message = Message.parse(data)
        if message.method == "REGISTER":
            if not message.get("authorization"):
                self.reply(
                    message,
                    peer,
                    401,
                    "Unauthorized",
                    [
                        'WWW-Authenticate: Digest realm="synthetic", nonce="nonce", qop="auth"'
                    ],
                )
                return
            auth = challenge_fields(message.get("authorization"))
            expected = challenge_fields(
                digest(
                    self.account.username,
                    self.account.password,
                    "sip:" + self.account.host,
                    {"realm": "synthetic", "nonce": "nonce", "qop": "auth"},
                    int(auth["nc"], 16),
                    auth["cnonce"],
                )
            )
            assert auth["response"] == expected["response"]
            self.receiver = peer
            self.registrations.append(message)
            self.reply(
                message,
                peer,
                200,
                "OK",
                [
                    "Contact: " + message.get("contact"),
                    "Expires: " + message.get("expires"),
                ],
            )
            return
        if message.method == "BYE":
            self.reply(message, peer, 200, "OK")
        self.messages.put_nowait((data, peer))

    def reply(self, m, peer, code, reason, extra=()):
        headers = (
            [f"SIP/2.0 {code} {reason}"]
            + [f"Via: {v}" for v in m.headers["via"]]
            + [
                f"From: {m.get('from')}",
                f"To: {m.get('to')}",
                f"Call-ID: {m.get('call-id')}",
                f"CSeq: {m.get('cseq')}",
            ]
            + list(extra)
        )
        self.transport.sendto(wire(headers), peer)

    def request(self, method, body="", tag=None):
        to = f"<sip:synthetic-user@{self.account.host}>" + (
            (";tag=" + tag) if tag else ""
        )
        seq = "10" if method in {"INVITE", "ACK", "CANCEL"} else "11"
        branch = self.branch if method in {"INVITE", "CANCEL"} else "z9hG4bK-" + method
        lines = [
            f"{method} sip:synthetic-user@{self.account.host} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.ip}:{self.address[1]};branch={branch}",
            f"From: <sip:gate@{self.account.host}>;tag={self.local_tag}",
            f"To: {to}",
            f"Call-ID: {self.call_id}",
            f"CSeq: {seq} {method}",
            f"Contact: <sip:gate@{self.account.host}>",
        ]
        if body:
            lines += ["Content-Type: application/sdp"]
        self.transport.sendto(wire(lines, body), self.receiver)

    async def response(self, status, method=None):
        async with asyncio.timeout(10):
            while True:
                data, _ = await self.messages.get()
                m = Message.parse(data)
                if m.status == status and (
                    method is None or m.get("cseq").endswith(" " + method)
                ):
                    return m


async def eventually(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def simulated_gate(socket_enabled, monkeypatch):
    import pytest_socket

    pytest_socket.socket_allow_hosts(REAL_ADDRESSES + ["127.0.0.1"])
    monkeypatch.setattr("aioice.ice.get_host_addresses", lambda **kwargs: REAL_IPV4)
    ip = REAL_IPV4[0]
    loop = asyncio.get_running_loop()
    gate = Gate(ip)
    await loop.create_datagram_endpoint(lambda: gate, local_addr=(ip, 0))
    gate.account.host = f"{ip}:{gate.address[1]}"

    async def account():
        return gate.account

    manager = CallManager(account, stun_server=None)
    await manager.start()
    await eventually(lambda: manager.registered)
    try:
        yield gate, manager
    finally:
        await manager.stop()
        gate.transport.close()
        await asyncio.sleep(0.05)


def offer(ip, audio_port, video_port=None):
    text = f"v=0\r\no=- 1 1 IN IP4 {ip}\r\ns=simulated gate\r\nc=IN IP4 {ip}\r\nt=0 0\r\nm=audio {audio_port} RTP/AVP 0\r\na=sendrecv\r\n"
    if video_port:
        text += f"m=video {video_port} RTP/AVP 99\r\na=rtpmap:99 H264/90000\r\na=fmtp:99 packetization-mode=1\r\na=sendonly\r\n"
    return text


async def test_registration_retransmission_cancel_and_cleanup(simulated_gate):
    gate, manager = simulated_gate
    assert manager.registered
    gate.request("INVITE", offer(gate.ip, 40000))
    await gate.response(180)
    assert manager.ring_sequence == 1
    gate.request("INVITE", offer(gate.ip, 40000))
    await gate.response(180)
    assert manager.ring_sequence == 1
    gate.request("CANCEL")
    await gate.response(200, "CANCEL")
    await gate.response(487, "INVITE")
    await eventually(lambda: manager.call is None)
    assert manager.state == "idle"
    await manager.stop()
    assert gate.registrations[-1].get("expires") == "0"
    assert "*" not in gate.registrations[-1].get("contact")
    assert not manager.tasks and not manager.pending and not manager.sessions
    # Disabling/re-enabling calls reuses the manager and must register again.
    await manager.start()
    await eventually(lambda: manager.registered)
    assert not manager.closed
    await manager.stop()
    assert gate.registrations[-1].get("expires") == "0"


class Microphone(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.samples = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        tone = array.array(
            "h",
            (
                int(8000 * math.sin(2 * math.pi * 900 * (self.samples + i) / 48000))
                for i in range(960)
            ),
        )
        frame.planes[0].update(tone.tobytes())
        frame.sample_rate = 48000
        frame.pts = self.samples
        frame.time_base = Fraction(1, 48000)
        self.samples += 960
        return frame


async def test_real_rtp_video_audio_and_microphone_return(simulated_gate):
    gate, manager = simulated_gate
    loop = asyncio.get_running_loop()
    audio = Port()
    video = Port()
    await loop.create_datagram_endpoint(lambda: audio, local_addr=(gate.ip, 0))
    await loop.create_datagram_endpoint(lambda: video, local_addr=(gate.ip, 0))
    browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received = {}
    sender_task = None

    @browser.on("track")
    def received_track(track):
        received[track.kind] = track

    try:
        gate.request("INVITE", offer(gate.ip, audio.address[1], video.address[1]))
        await gate.response(180)
        browser.addTrack(Microphone())
        browser.addTransceiver("video", direction="recvonly")
        await browser.setLocalDescription(await browser.createOffer())
        answer = await manager.offer(browser.localDescription.sdp, "synthetic-viewer")
        accepted = await gate.response(200, "INVITE")
        tag = accepted.get("to").split(";tag=")[1]
        assert "H264/90000" in accepted.body
        assert "a=recvonly" in accepted.body and "a=sendrecv" in accepted.body
        gate.request("ACK", tag=tag)
        await browser.setRemoteDescription(
            RTCSessionDescription(sdp=answer, type="answer")
        )
        await eventually(lambda: manager.state == "active")
        a = next(c for c in manager.call.bridge.channels if c.track.kind == "audio")
        v = next(c for c in manager.call.bridge.channels if c.track.kind == "video")
        audio_codec = RTCRtpCodecParameters(
            mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
        )
        video_codec = RTCRtpCodecParameters(
            mimeType="video/H264", clockRate=90000, payloadType=99
        )
        audio_encoder = get_encoder(audio_codec)
        video_encoder = get_encoder(video_codec)

        async def send_media():
            sequence = 0
            for i in range(160):
                frame = av.AudioFrame(format="s16", layout="mono", samples=160)
                frame.planes[0].update(
                    array.array(
                        "h",
                        (
                            int(
                                9000
                                * math.sin(2 * math.pi * 400 * (i * 160 + j) / 8000)
                            )
                            for j in range(160)
                        ),
                    ).tobytes()
                )
                frame.sample_rate = 8000
                frame.pts = i * 160
                frame.time_base = Fraction(1, 8000)
                payloads, stamp = await loop.run_in_executor(
                    None, audio_encoder.encode, frame
                )
                for payload in payloads:
                    audio.transport.sendto(
                        RtpPacket(
                            payload_type=0,
                            sequence_number=i,
                            timestamp=stamp,
                            ssrc=42,
                            payload=payload,
                        ).serialize(),
                        a.advertised,
                    )
                if i % 3 == 0:
                    frame = av.VideoFrame(160, 120, "yuv420p")
                    for plane in frame.planes:
                        plane.update(bytes([100]) * plane.buffer_size)
                    frame.pts = i * 1800
                    frame.time_base = Fraction(1, 90000)
                    payloads, stamp = await loop.run_in_executor(
                        None, video_encoder.encode, frame, i == 0
                    )
                    for j, payload in enumerate(payloads):
                        packet = RtpPacket(
                            payload_type=99,
                            sequence_number=sequence,
                            timestamp=stamp,
                            ssrc=43,
                            payload=payload,
                        )
                        packet.marker = int(j == len(payloads) - 1)
                        sequence += 1
                        video.transport.sendto(packet.serialize(), v.advertised)
                await asyncio.sleep(0.02)

        sender_task = asyncio.create_task(send_media())
        await eventually(lambda: set(received) == {"audio", "video"})
        audio_frame, video_frame = await asyncio.wait_for(
            asyncio.gather(received["audio"].recv(), received["video"].recv()), 10
        )
        assert video_frame.width == 160 and video_frame.height == 120
        assert audio_frame.samples > 0
        # Decode the browser's microphone returned as actual SIP-side PCMU RTP.
        decoder = get_decoder(audio_codec)
        heard = False
        async with asyncio.timeout(10):
            while not heard:
                packet, _ = await audio.messages.get()
                rtp = RtpPacket.parse(packet)
                decoded = decoder.decode(JitterFrame(rtp.payload, rtp.timestamp))
                for frame in decoded:
                    samples = array.array("h", bytes(frame.planes[0]))
                    heard = sum(abs(s) for s in samples) / len(samples) > 100
        assert heard and a.packets_received > 0 and a.packets_sent > 0
        # Closing a viewer must leave CellGate's call and source tracks alive.
        original_call = manager.call
        await manager.close_viewer("synthetic-viewer")
        await browser.close()
        assert manager.call is original_call and manager.call.answered
        assert a.track.readyState == "live" and v.track.readyState == "live"
        assert not manager.sessions
        browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        received_again = {}

        @browser.on("track")
        def reopened_track(track):
            received_again[track.kind] = track

        browser.addTransceiver("audio", direction="recvonly")
        browser.addTransceiver("video", direction="recvonly")
        await browser.setLocalDescription(await browser.createOffer())
        reopened = await manager.offer(browser.localDescription.sdp, "second-viewer")
        await browser.setRemoteDescription(
            RTCSessionDescription(sdp=reopened, type="answer")
        )
        await eventually(lambda: set(received_again) == {"audio", "video"})
        next_audio, next_video = await asyncio.wait_for(
            asyncio.gather(
                received_again["audio"].recv(), received_again["video"].recv()
            ),
            10,
        )
        assert next_video.width == 160 and next_audio.samples > 0
        assert manager.call is original_call and manager.ring_sequence == 1
        gate.request("BYE", tag=tag)
        await gate.response(200, "BYE")
        await eventually(lambda: manager.call is None and not manager.sessions)
        assert a.closed and v.closed
        assert not a.tasks and not v.tasks
    finally:
        if sender_task:
            sender_task.cancel()
            await asyncio.gather(sender_task, return_exceptions=True)
        await browser.close()
        audio.transport.close()
        video.transport.close()


async def test_stun_uses_the_same_rtp_socket(simulated_gate):
    from aioice import stun
    from custom_components.cellgate.media import DatagramPort

    gate, _ = simulated_gate

    class StunServer(Port):
        def datagram_received(self, data, peer):
            request = stun.parse_message(data)
            response = stun.Message(
                message_method=stun.Method.BINDING,
                message_class=stun.Class.RESPONSE,
                transaction_id=request.transaction_id,
                attributes={"XOR-MAPPED-ADDRESS": ("203.0.113.55", 45678)},
            )
            self.transport.sendto(bytes(response), peer)

    loop = asyncio.get_running_loop()
    server = StunServer()
    rtp = DatagramPort(lambda data, addr: None)
    await loop.create_datagram_endpoint(lambda: server, local_addr=(gate.ip, 0))
    await loop.create_datagram_endpoint(lambda: rtp, local_addr=(gate.ip, 0))
    try:
        assert await rtp.mapped_address(server.address) == ("203.0.113.55", 45678)
    finally:
        rtp.close()
        server.transport.close()
