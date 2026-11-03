"""Negotiate the supported SIP RTP profile; never infer codecs from config IDs."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import ipaddress
import secrets

from aiortc.rtcrtpparameters import RTCRtpCodecParameters


class MediaError(Exception):
    """Safe media negotiation error, excluding SDP and key material."""


@dataclass(repr=False)
class MediaSection:
    kind: str
    port: int
    protocol: str
    formats: list[str]
    host: str = ""
    attributes: list[str] = field(default_factory=list)

    def values(self, key):
        return [a[len(key) + 1 :] for a in self.attributes if a.startswith(key + ":")]


@dataclass(repr=False)
class NegotiatedMedia:
    section: MediaSection
    codec: RTCRtpCodecParameters
    remote: tuple[str, int]
    rtcp_remote: tuple[str, int]
    receive: bool
    transmit: bool
    remote_key: bytes | None = None
    local_key: bytes | None = None
    crypto_tag: str | None = None


def parse_offer(text: str) -> list[MediaSection]:
    if not isinstance(text, str) or len(text) > 60000 or "\x00" in text:
        raise MediaError("Invalid SIP media offer")
    sections = []
    current = None
    host = ""
    inherited = []
    try:
        for line in text.replace("\r\n", "\n").splitlines():
            if line.startswith("m="):
                fields = line[2:].split()
                if len(fields) < 4 or "/" in fields[1]:
                    raise ValueError
                port = int(fields[1])
                if not 0 <= port <= 65535:
                    raise ValueError
                current = MediaSection(
                    fields[0], port, fields[2], fields[3:], host, list(inherited)
                )
                sections.append(current)
            elif line.startswith("c="):
                fields = line[2:].split()
                if len(fields) != 3 or fields[:2] != ["IN", "IP4"]:
                    raise MediaError("Only IPv4 SIP media is supported")
                address = str(ipaddress.IPv4Address(fields[2]))
                if current:
                    current.host = address
                else:
                    host = address
            elif line.startswith("a="):
                (current.attributes if current else inherited).append(line[2:])
        if not sections or len(sections) > 8:
            raise ValueError
        return sections
    except (ValueError, IndexError):
        raise MediaError("Invalid SIP media offer") from None


def negotiate(section: MediaSection) -> NegotiatedMedia | None:
    if section.kind not in {"audio", "video"} or not section.port:
        return None
    if section.protocol not in {"RTP/AVP", "RTP/AVPF", "RTP/SAVP", "RTP/SAVPF"}:
        raise MediaError("Unsupported SIP media transport")
    if any(
        a.startswith(("ice-ufrag:", "fingerprint:")) or a == "rtcp-mux-only"
        for a in section.attributes
    ):
        raise MediaError(
            "SIP ICE/DTLS and mandatory RTP multiplexing are not supported"
        )
    try:
        address = ipaddress.IPv4Address(section.host)
        if (
            address.is_loopback
            or address.is_multicast
            or address.is_link_local
            or address.is_unspecified
        ):
            raise ValueError
    except ValueError:
        raise MediaError("Invalid SIP media address") from None
    static = {
        0: ("PCMU", 8000, 1),
        8: ("PCMA", 8000, 1),
        9: ("G722", 8000, 1),
        34: ("H263", 90000, None),
    }
    codecs = {}
    parameters = {}
    try:
        for value in section.values("rtpmap"):
            number, spec = value.split(None, 1)
            name, rate, *channels = spec.split("/")
            codecs[int(number)] = (
                name.upper(),
                int(rate),
                int(channels[0])
                if channels
                else (1 if section.kind == "audio" else None),
            )
        for value in section.values("fmtp"):
            number, spec = value.split(None, 1)
            parameters[int(number)] = dict(
                p.strip().split("=", 1) for p in spec.split(";") if "=" in p
            )
        selected = None
        for value in section.formats:
            payload = int(value)
            if not 0 <= payload <= 127:
                raise ValueError
            spec = codecs.get(payload, static.get(payload))
            if not spec:
                continue
            name, rate, channels = spec
            supported = (
                section.kind == "audio"
                and name in {"PCMU", "PCMA", "G722"}
                and rate == 8000
                and channels == 1
            ) or (section.kind == "video" and name in {"H264", "VP8"} and rate == 90000)
            if name == "H264" and parameters.get(payload, {}).get(
                "packetization-mode", "0"
            ) not in {"0", "1"}:
                supported = False
            if supported:
                selected = RTCRtpCodecParameters(
                    mimeType=section.kind + "/" + name,
                    clockRate=rate,
                    channels=channels,
                    payloadType=payload,
                    parameters=parameters.get(payload, {}),
                )
                break
        if selected is None:
            return None
        remote_key = local_key = tag = None
        crypto = section.values("crypto")
        if section.protocol in {"RTP/SAVP", "RTP/SAVPF"}:
            for value in crypto:
                fields = value.split()
                if (
                    len(fields) == 3
                    and fields[1] == "AES_CM_128_HMAC_SHA1_80"
                    and fields[2].startswith("inline:")
                    and "|" not in fields[2]
                ):
                    decoded = base64.b64decode(fields[2][7:], validate=True)
                    if len(decoded) == 30 and fields[0].isdigit():
                        remote_key, local_key, tag = (
                            decoded,
                            secrets.token_bytes(30),
                            fields[0],
                        )
                        break
            if remote_key is None:
                raise MediaError("Unsupported SIP SRTP key negotiation")
        elif crypto:
            raise MediaError("Ambiguous SIP encryption offer")
        direction = next(
            (
                a
                for a in section.attributes
                if a in {"sendrecv", "sendonly", "recvonly", "inactive"}
            ),
            "sendrecv",
        )
        rtcp = (section.host, section.port + 1)
        if values := section.values("rtcp"):
            fields = values[0].split()
            rtcp = (
                fields[3]
                if len(fields) == 4 and fields[1:3] == ["IN", "IP4"]
                else section.host,
                int(fields[0]),
            )
        if not 1 <= rtcp[1] <= 65535:
            raise ValueError
        rtcp_ip = ipaddress.IPv4Address(rtcp[0])
        if (
            rtcp_ip.is_loopback
            or rtcp_ip.is_multicast
            or rtcp_ip.is_link_local
            or rtcp_ip.is_unspecified
        ):
            raise ValueError
        return NegotiatedMedia(
            section,
            selected,
            (section.host, section.port),
            rtcp,
            direction in {"sendrecv", "sendonly"},
            section.kind == "audio" and direction in {"sendrecv", "recvonly"},
            remote_key,
            local_key,
            tag,
        )
    except (ValueError, IndexError):
        raise MediaError("Invalid SIP media configuration") from None


def answer_sdp(sections, channels):
    session = secrets.randbits(48)
    lines = ["v=0", f"o=- {session} 1 IN IP4 0.0.0.0", "s=CellGate", "t=0 0"]
    for section in sections:
        channel = next((c for c in channels if c.spec.section is section), None)
        if channel is None:
            lines += [
                f"m={section.kind} 0 {section.protocol} {section.formats[0]}",
                "c=IN IP4 0.0.0.0",
            ]
            continue
        spec, codec = channel.spec, channel.spec.codec
        host, port = channel.advertised
        rtcp_host, rtcp_port = channel.rtcp_advertised
        lines += [
            f"m={section.kind} {port} {section.protocol} {codec.payloadType}",
            f"c=IN IP4 {host}",
            f"a=rtpmap:{codec.payloadType} {codec.name}/{codec.clockRate}"
            + (
                "/" + str(codec.channels)
                if codec.channels and codec.channels != 1
                else ""
            ),
            f"a=rtcp:{rtcp_port} IN IP4 {rtcp_host}",
        ]
        if codec.parameters:
            lines += [
                f"a=fmtp:{codec.payloadType} "
                + ";".join(f"{k}={v}" for k, v in codec.parameters.items())
            ]
        direction = (
            "sendrecv"
            if spec.receive and spec.transmit
            else "recvonly"
            if spec.receive
            else "sendonly"
            if spec.transmit
            else "inactive"
        )
        lines += ["a=" + direction]
        if spec.local_key:
            lines += [
                f"a=crypto:{spec.crypto_tag} AES_CM_128_HMAC_SHA1_80 inline:"
                + base64.b64encode(spec.local_key).decode()
            ]
    return "\r\n".join(lines) + "\r\n"
