"""SIP RTP/SRTP to aiortc decoded media tracks, with microphone return."""

from __future__ import annotations

import asyncio
import base64
from fractions import Fraction
import secrets
import socket

from aioice import stun
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaRelay
from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterBuffer
from aiortc.mediastreams import MediaStreamError
from aiortc.rtp import RtpPacket, RtcpPacket, RtcpPsfbPacket, RtcpRrPacket
from av import AudioFrame, AudioResampler
import pylibsrtp

from .sip_sdp import MediaError, answer_sdp, negotiate


class FrameTrack(MediaStreamTrack):
    """Bounded real media only; no placeholder frames or snapshots."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.frames = asyncio.Queue(maxsize=4 if kind == "audio" else 2)

    def put(self, frame):
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    async def recv(self):
        frame = await self.frames.get()
        if frame is None or self.readyState != "live":
            raise MediaStreamError
        return frame

    def stop(self):
        super().stop()
        self.put(None)


class DatagramPort(asyncio.DatagramProtocol):
    def __init__(self, callback):
        self.callback = callback
        self.transport = None
        self.binding = None
        self.stun_peer = None
        self.stun_id = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if self.binding is not None and addr == self.stun_peer:
            try:
                response = stun.parse_message(data)
                if (
                    response.transaction_id == self.stun_id
                    and response.message_class == stun.Class.RESPONSE
                ):
                    if not self.binding.done():
                        self.binding.set_result(
                            response.attributes["XOR-MAPPED-ADDRESS"]
                        )
                    return
            except (ValueError, KeyError):
                pass
        self.callback(data, addr)

    async def mapped_address(self, server):
        local = self.transport.get_extra_info("sockname")[:2]
        if not server:
            return local
        loop = asyncio.get_running_loop()
        try:
            resolved = await loop.getaddrinfo(
                server[0], server[1], family=socket.AF_INET, type=socket.SOCK_DGRAM
            )
            self.stun_peer = resolved[0][4][:2]
            request = stun.Message(
                message_method=stun.Method.BINDING, message_class=stun.Class.REQUEST
            )
            self.stun_id = request.transaction_id
            self.binding = loop.create_future()
            for _ in range(3):
                self.transport.sendto(bytes(request), self.stun_peer)
                try:
                    return await asyncio.wait_for(asyncio.shield(self.binding), 1)
                except TimeoutError:
                    pass
            raise MediaError(
                "STUN mapping failed; check network connectivity or call network settings"
            )
        except OSError:
            raise MediaError("Unable to resolve the STUN server") from None
        finally:
            if self.binding and not self.binding.done():
                self.binding.cancel()
            self.binding = None

    def send(self, packet, remote):
        if self.transport and not self.transport.is_closing():
            self.transport.sendto(packet, remote)

    def close(self):
        if self.transport:
            self.transport.close()


def create_decoder(codec):
    decoder = get_decoder(codec)
    if codec.name == "H264" and (value := codec.parameters.get("sprop-parameter-sets")):
        try:
            parts = str(value).split(",")
            if len(parts) > 8:
                raise ValueError
            data = [base64.b64decode(part, validate=True) for part in parts]
            if any(not item or len(item) > 16384 for item in data):
                raise ValueError
            decoder.codec.extradata = b"".join(
                b"\x00\x00\x00\x01" + item for item in data
            )
        except ValueError:
            raise MediaError("Invalid H264 parameter sets") from None
    return decoder


class RtpChannel:
    def __init__(self, spec):
        self.spec = spec
        self.track = FrameTrack(spec.section.kind)
        self.incoming = asyncio.Queue(maxsize=256)
        self.microphone = asyncio.Queue(maxsize=4)
        self.tasks = set()
        self.rtp = DatagramPort(self.receive_rtp)
        self.rtcp = DatagramPort(self.receive_rtcp)
        self.advertised = self.rtcp_advertised = None
        self.ssrc = secrets.randbits(32)
        self.remote_ssrc = None
        self.sequence = secrets.randbits(16)
        self.timestamp_base = secrets.randbits(32)
        self.decoder = None
        self.encoder = None
        self.srtp_in = self.srtp_out = None
        self.first_timestamp = None
        self.previous_timestamp = None
        self.timestamp_cycles = 0
        self.closed = False
        self.accepting = False
        self.packets_received = self.packets_sent = 0
        self.jitter = JitterBuffer(
            128 if self.track.kind == "video" else 16,
            prefetch=0,
            is_video=self.track.kind == "video",
        )
        if spec.remote_key:
            self.srtp_in = pylibsrtp.Session(
                pylibsrtp.Policy(
                    key=spec.remote_key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND
                )
            )
            self.srtp_out = pylibsrtp.Session(
                pylibsrtp.Policy(
                    key=spec.local_key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_OUTBOUND
                )
            )

    async def open(self, local_ip, stun_server):
        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(
                lambda: self.rtp, local_addr=(local_ip, 0)
            )
            await loop.create_datagram_endpoint(
                lambda: self.rtcp, local_addr=(local_ip, 0)
            )
            self.advertised, self.rtcp_advertised = await asyncio.gather(
                self.rtp.mapped_address(stun_server),
                self.rtcp.mapped_address(stun_server),
            )
            if self.spec.receive:
                self.decoder = await loop.run_in_executor(
                    None, create_decoder, self.spec.codec
                )
        except BaseException:
            self.rtp.close()
            self.rtcp.close()
            raise

    def start(self):
        self.accepting = True
        if self.spec.receive:
            self.spawn(self.decode())
        if self.spec.transmit:
            self.spawn(self.transmit_audio())
        self.spawn(self.feedback())

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        # Workers consume malformed media themselves. Unexpected failures end
        # this media track without logging packets, SDP, or key material.
        def done(completed):
            if not completed.cancelled() and completed.exception():
                self.track.stop()

        task.add_done_callback(done)

    def receive_rtp(self, data, addr):
        if (
            not self.accepting
            or not self.spec.receive
            or addr[0] != self.spec.remote[0]
        ):
            return
        try:
            if self.srtp_in:
                data = self.srtp_in.unprotect(data)
            packet = RtpPacket.parse(data)
            if packet.payload_type != self.spec.codec.payloadType:
                return
            if self.remote_ssrc is not None and packet.ssrc != self.remote_ssrc:
                return
            # Symmetric RTP learns a port only after a valid packet from the
            # negotiated IP (and successful authentication for SRTP).
            self.spec.remote = (addr[0], addr[1])
            self.remote_ssrc = packet.ssrc
            self.packets_received += 1
            packet._data = depayload(self.spec.codec, packet.payload)
            if self.incoming.full():
                self.incoming.get_nowait()
            self.incoming.put_nowait(packet)
        except (ValueError, pylibsrtp.Error):
            return

    def receive_rtcp(self, data, addr):
        if not self.accepting or addr[0] != self.spec.rtcp_remote[0]:
            return
        try:
            if self.srtp_in:
                data = self.srtp_in.unprotect_rtcp(data)
            RtcpPacket.parse(data)
            self.spec.rtcp_remote = (addr[0], addr[1])
        except (ValueError, pylibsrtp.Error):
            return

    async def decode(self):
        loop = asyncio.get_running_loop()
        while True:
            packet = await self.incoming.get()
            pli, encoded = self.jitter.add(packet)
            if pli:
                self.send_pli()
            if encoded is None:
                continue
            raw = encoded.timestamp
            if (
                self.previous_timestamp is not None
                and self.previous_timestamp - raw > 0x80000000
            ):
                self.timestamp_cycles += 1 << 32
            self.previous_timestamp = raw
            unwrapped = raw + self.timestamp_cycles
            if self.first_timestamp is None:
                self.first_timestamp = unwrapped
            encoded.timestamp = unwrapped - self.first_timestamp
            try:
                frames = await loop.run_in_executor(None, self.decoder.decode, encoded)
                for frame in frames:
                    self.track.put(frame)
            except Exception:
                # Decode failure is recoverable (e.g. joining before a keyframe).
                self.send_pli()

    def send_pli(self):
        if self.track.kind == "video" and self.remote_ssrc is not None:
            self.send_rtcp(
                bytes(
                    RtcpPsfbPacket(fmt=1, ssrc=self.ssrc, media_ssrc=self.remote_ssrc)
                )
            )

    def send_rtcp(self, data):
        if self.srtp_out:
            data = self.srtp_out.protect_rtcp(data)
        self.rtcp.send(data, self.spec.rtcp_remote)

    async def feedback(self):
        while True:
            self.send_rtcp(bytes(RtcpRrPacket(ssrc=self.ssrc, reports=[])))
            self.send_pli()
            await asyncio.sleep(5)

    async def feed_microphone(self, track):
        try:
            while True:
                frame = await track.recv()
                if self.microphone.full():
                    self.microphone.get_nowait()
                self.microphone.put_nowait(frame)
        except MediaStreamError:
            return

    async def transmit_audio(self):
        loop = asyncio.get_running_loop()
        encoder = await loop.run_in_executor(None, get_encoder, self.spec.codec)
        resampler = AudioResampler(
            format="s16", layout="mono", rate=8000, frame_size=160
        )
        samples = 0
        while True:
            try:
                frame = await asyncio.wait_for(self.microphone.get(), 0.02)
                frame.pts = None
                frames = await loop.run_in_executor(None, resampler.resample, frame)
            except TimeoutError:
                silence = AudioFrame(format="s16", layout="mono", samples=160)
                silence.planes[0].update(bytes(silence.planes[0].buffer_size))
                silence.sample_rate = 8000
                frames = [silence]
            for frame in frames:
                frame.pts = samples
                frame.time_base = Fraction(1, 8000)
                samples += frame.samples
                payloads, timestamp = await loop.run_in_executor(
                    None, encoder.encode, frame
                )
                for i, payload in enumerate(payloads):
                    packet = RtpPacket(
                        payload_type=self.spec.codec.payloadType,
                        sequence_number=self.sequence,
                        timestamp=(self.timestamp_base + timestamp + i * 160)
                        & 0xFFFFFFFF,
                        ssrc=self.ssrc,
                        payload=payload,
                    )
                    self.sequence = (self.sequence + 1) & 0xFFFF
                    data = packet.serialize()
                    if self.srtp_out:
                        data = self.srtp_out.protect(data)
                    self.rtp.send(data, self.spec.remote)
                    self.packets_sent += 1

    async def close(self):
        self.closed = True
        self.accepting = False
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.rtp.close()
        self.rtcp.close()
        self.track.stop()
        self.srtp_in = self.srtp_out = None
        self.spec.remote_key = self.spec.local_key = None


class MediaBridge:
    def __init__(self, sections):
        self.sections = sections
        self.relay = MediaRelay()
        self.channels = []
        kinds = set()
        for section in sections:
            spec = negotiate(section)
            if spec and section.kind not in kinds:
                self.channels.append(RtpChannel(spec))
                kinds.add(section.kind)
        if not any(c.spec.receive for c in self.channels):
            raise MediaError("The intercom offered no supported incoming audio/video")
        self.sdp = None

    @property
    def can_talk(self):
        return any(c.spec.transmit for c in self.channels)

    async def prepare(self, local_ip, stun_server):
        opening = [
            asyncio.create_task(c.open(local_ip, stun_server)) for c in self.channels
        ]
        try:
            await asyncio.gather(*opening)
            self.sdp = answer_sdp(self.sections, self.channels)
            return self.sdp
        except BaseException:
            # A failed socket/STUN operation must not leave sibling open tasks
            # creating transports after cleanup has already completed.
            for task in opening:
                task.cancel()
            await asyncio.gather(*opening, return_exceptions=True)
            await self.close()
            raise

    def start(self):
        for channel in self.channels:
            channel.start()

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.channels))
