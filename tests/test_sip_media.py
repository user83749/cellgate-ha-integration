"""SDP negotiation, media encryption and bounded queues using synthetic data."""

import base64
import pytest
from aiortc.rtp import RtpPacket

from custom_components.cellgate.sip_sdp import (
    MediaError,
    parse_offer,
    negotiate,
)
from custom_components.cellgate.media import RtpChannel
from custom_components.cellgate.sip import Message, SipError


def sdp(
    kind="video",
    formats="99 34",
    attributes="a=rtpmap:99 H264/90000\r\na=fmtp:99 packetization-mode=1\r\n",
    protocol="RTP/AVP",
):
    return (
        f"v=0\r\no=- 1 1 IN IP4 203.0.113.1\r\ns=test\r\nc=IN IP4 203.0.113.1\r\nt=0 0\r\nm={kind} 4000 {protocol} {formats}\r\n"
        + attributes
    )


def test_dynamic_payloads_are_read_from_sdp():
    section = parse_offer(sdp())[0]
    result = negotiate(section)
    assert result.codec.name == "H264"
    assert result.codec.payloadType == 99
    assert result.receive and not result.transmit
    changed = negotiate(parse_offer(sdp(attributes="a=rtpmap:99 VP8/90000\r\n"))[0])
    assert changed.codec.name == "VP8"


def test_h263_only_is_rejected():
    assert negotiate(parse_offer(sdp(formats="34", attributes=""))[0]) is None


@pytest.mark.parametrize("payload,name", [(0, "PCMU"), (8, "PCMA"), (9, "G722")])
def test_audio_static_payloads(payload, name):
    result = negotiate(parse_offer(sdp("audio", str(payload), ""))[0])
    assert result.codec.name == name
    assert result.transmit and result.receive


@pytest.mark.parametrize(
    "direction,receive,transmit",
    [("sendonly", True, False), ("recvonly", False, True), ("inactive", False, False)],
)
def test_audio_directions(direction, receive, transmit):
    result = negotiate(parse_offer(sdp("audio", "0", "a=" + direction + "\r\n"))[0])
    assert result.receive is receive
    assert result.transmit is transmit


@pytest.mark.parametrize(
    "protocol,attributes",
    [
        ("UDP/TLS/RTP/SAVPF", ""),
        ("RTP/AVP", "a=ice-ufrag:secret\r\n"),
        ("RTP/AVP", "a=rtcp-mux-only\r\n"),
        ("RTP/SAVP", ""),
    ],
)
def test_unsupported_profiles_do_not_downgrade(protocol, attributes):
    with pytest.raises(MediaError):
        negotiate(parse_offer(sdp("audio", "0", attributes, protocol))[0])


def test_sdes_srtp_roundtrip_and_private_repr():
    key = bytes(range(30))
    encoded = base64.b64encode(key).decode()
    offer = sdp(
        "audio",
        "0",
        f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{encoded}\r\n",
        "RTP/SAVP",
    )
    result = negotiate(parse_offer(offer)[0])
    channel = RtpChannel(result)
    assert encoded not in repr(result)
    import pylibsrtp

    remote = pylibsrtp.Session(
        pylibsrtp.Policy(key=key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_OUTBOUND)
    )
    packet = RtpPacket(
        payload_type=0, sequence_number=1, timestamp=160, ssrc=42, payload=b"\xff" * 160
    )
    channel.accepting = True
    channel.receive_rtp(remote.protect(packet.serialize()), ("203.0.113.1", 4000))
    assert channel.packets_received == 1
    channel.receive_rtp(packet.serialize(), ("203.0.113.1", 4000))
    assert channel.packets_received == 1
    channel.track.stop()


def test_media_source_filter_and_bounded_queue():
    spec = negotiate(parse_offer(sdp("audio", "0", ""))[0])
    channel = RtpChannel(spec)
    channel.accepting = True
    p = RtpPacket(
        payload_type=0, sequence_number=1, timestamp=1, ssrc=1, payload=b"\xff" * 160
    ).serialize()
    channel.receive_rtp(p, ("203.0.113.99", 4000))
    assert channel.packets_received == 0
    for _ in range(400):
        channel.receive_rtp(p, ("203.0.113.1", 4000))
    assert channel.incoming.qsize() == 256
    channel.track.stop()


def test_no_sdp_keys_in_invalid_offer_errors():
    with pytest.raises(MediaError) as err:
        negotiate(parse_offer(sdp("audio", "0", "a=crypto:secret\r\n", "RTP/SAVP"))[0])
    assert "secret" not in str(err.value)


def test_sip_framing_checks_and_secret_safe_representation():
    raw = b"INVITE sip:u@host SIP/2.0\r\nVia: SIP/2.0/UDP host;branch=b\r\nFrom: <sip:caller@host>;tag=t\r\nTo: <sip:u@host>\r\nCall-ID: secret-call-id\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n"
    m = Message.parse(raw)
    assert m.method == "INVITE"
    assert "secret" not in repr(m)
    with pytest.raises(SipError):
        Message.parse(raw.replace(b"Content-Length: 0", b"Content-Length: 5"))
    with pytest.raises(SipError):
        Message.parse(
            raw.replace(b"CSeq: 1 INVITE", b"CSeq: 1 INVITE\r\nCall-ID: duplicate")
        )


async def test_partial_media_prepare_failure_closes_siblings(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from custom_components.cellgate.media import MediaBridge

    sections = parse_offer(
        sdp("audio", "0", "") + "m=video 5000 RTP/AVP 99\r\na=rtpmap:99 H264/90000\r\n"
    )
    bridge = MediaBridge(sections)
    cancelled = asyncio.Event()

    async def slow_open(*args):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    monkeypatch.setattr(
        bridge.channels[0],
        "open",
        AsyncMock(side_effect=MediaError("Synthetic STUN failure")),
    )
    monkeypatch.setattr(bridge.channels[1], "open", slow_open)
    with pytest.raises(MediaError):
        await bridge.prepare("192.0.2.1", None)
    assert cancelled.is_set()
    assert all(c.closed for c in bridge.channels)


def test_h264_out_of_band_parameter_sets():
    from custom_components.cellgate.media import create_decoder

    section = parse_offer(
        sdp(
            attributes="a=rtpmap:99 H264/90000\r\na=fmtp:99 packetization-mode=1;sprop-parameter-sets=Z0IAHw==,aM4G4g==\r\n"
        )
    )[0]
    decoder = create_decoder(negotiate(section).codec)
    assert decoder.codec.extradata.startswith(b"\x00\x00\x00\x01g")


def test_srtp_return_path_encrypts_with_our_negotiated_key():
    import pylibsrtp

    key = base64.b64encode(bytes(range(30))).decode()
    spec = negotiate(
        parse_offer(
            sdp(
                "audio",
                "0",
                f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{key}\r\n",
                "RTP/SAVP",
            )
        )[0]
    )
    channel = RtpChannel(spec)
    receiver = pylibsrtp.Session(
        pylibsrtp.Policy(
            key=spec.local_key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND
        )
    )
    packet = RtpPacket(
        payload_type=0, sequence_number=1, timestamp=160, ssrc=42, payload=b"\xff" * 160
    ).serialize()
    encrypted = channel.srtp_out.protect(packet)
    assert encrypted != packet
    assert receiver.unprotect(encrypted) == packet
    channel.track.stop()
