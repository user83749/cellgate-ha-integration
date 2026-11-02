"""Private Asterisk configuration; no shell interpolation or HA API access."""

from __future__ import annotations
import asyncio
import ipaddress
from pathlib import Path
import re
import secrets
import socket


class BackendError(Exception):
    """Safe, public error text only."""


def host_port(value):
    match = re.fullmatch(r"(?:sip:)?([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?", value or "")
    if not match or not 1 <= int(match[2] or 5060) <= 65535:
        raise BackendError("Unsupported SIP server address")
    return match[1], int(match[2] or 5060)


def config_value(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(c in value for c in "\r\n\x00[]")
    ):
        raise BackendError("Invalid SIP configuration")
    # Asterisk configuration escaping, including comment delimiters.
    return value.replace("\\", "\\\\").replace(";", r"\;")


async def resolve_next_hop(account):
    """Probe without credentials; Expires 0 and a unique contact cannot add a call receiver."""
    host, port = host_port(account["host"])
    loop = asyncio.get_running_loop()
    resolved = await loop.getaddrinfo(
        host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM
    )
    peer = resolved[0][4]
    queue = asyncio.Queue()

    class Replies(asyncio.DatagramProtocol):
        def datagram_received(self, data, source):
            if source == peer:
                queue.put_nowait(data)

    transport, _ = await loop.create_datagram_endpoint(Replies, remote_addr=peer)
    try:
        local = transport.get_extra_info("sockname")
        call_id = secrets.token_hex(16)
        uri = "sip:" + account["host"].removeprefix("sip:")
        from urllib.parse import quote

        user = quote(account["username"], safe="-_.!~*'()")
        message = (
            "\r\n".join(
                [
                    f"REGISTER {uri} SIP/2.0",
                    f"Via: SIP/2.0/UDP {local[0]}:{local[1]};branch=z9hG4bK{call_id};rport",
                    "Max-Forwards: 70",
                    f"From: <sip:{user}@{host}>;tag={call_id}",
                    f"To: <sip:{user}@{host}>",
                    f"Call-ID: {call_id}",
                    "CSeq: 1 REGISTER",
                    f"Contact: <sip:probe-{call_id}@{local[0]}:{local[1]}>",
                    "Expires: 0",
                    "Content-Length: 0",
                    "",
                    "",
                ]
            )
        ).encode()
        for delay in (0.5, 1, 2, 4):
            transport.sendto(message)
            try:
                async with asyncio.timeout(delay):
                    while True:
                        data = await queue.get()
                        text = data.decode(errors="replace")
                        if f"Call-ID: {call_id}" not in text:
                            continue
                        status = text.splitlines()[0].split()
                        if len(status) < 2 or not status[1].isdigit():
                            continue
                        code = int(status[1])
                        if code < 200:
                            continue
                        if code == 302:
                            match = re.search(
                                r"^Contact:\s*<?sip:([0-9.]+):([0-9]{1,5})(?:[;>\s]|$)",
                                text,
                                re.M | re.I,
                            )
                            if not match:
                                raise BackendError("Unsupported SIP redirect")
                            address = ipaddress.IPv4Address(match[1])
                            next_port = int(match[2])
                            if (
                                address.is_unspecified
                                or address.is_multicast
                                or not 1 <= next_port <= 65535
                            ):
                                raise BackendError("Invalid SIP redirect")
                            # A public registrar cannot redirect into the local network.
                            if (
                                not ipaddress.ip_address(peer[0]).is_private
                                and not address.is_global
                            ):
                                raise BackendError(
                                    "SIP redirect to private address refused"
                                )
                            return str(address), next_port
                        if code in (200, 401, 407):
                            return peer
                        raise BackendError("SIP registrar rejected discovery")
            except TimeoutError:
                continue
        raise BackendError("SIP registrar discovery timed out")
    finally:
        transport.close()


class Configuration:
    def __init__(self, root, options):
        self.root = Path(root)
        self.options = options
        self.ari_password = secrets.token_hex(32)
        self.sip_password = secrets.token_hex(32)
        self.http_port = int(options.get("asterisk_http_port", 18088))
        self.sip_port = int(options.get("sip_port", 15060))

    @property
    def ari_url(self):
        return f"http://127.0.0.1:{self.http_port}/ari"

    @property
    def ws_url(self):
        return f"http://127.0.0.1:{self.http_port}/ws"

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o600)

    def initialize(self):
        ports = [
            int(self.options.get(key, default))
            for key, default in (
                ("api_port", 28190),
                ("sip_port", 15060),
                ("asterisk_http_port", 18088),
                ("rtp_start", 28000),
                ("rtp_end", 28200),
            )
        ]
        api, sip, http, first, last = ports
        if (
            any(not 1 <= port <= 65535 for port in ports)
            or api == http
            or first % 2
            or last <= first
            or first <= sip <= last
        ):
            raise BackendError(
                "Configure distinct HTTP ports and an even-start RTP range outside the SIP port"
            )
        stun = self.options.get("stun_server", "stun.l.google.com:19302")
        if stun:
            host_port(stun)
        external = self.options.get("external_address", "")
        if external and not re.fullmatch(r"[A-Za-z0-9.-]+", external):
            raise BackendError(
                "External address must be an IP address or DNS hostname without a URL or port"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for name in ("run", "log", "state", "keys", "spool"):
            (self.root / name).mkdir(exist_ok=True)
        self.write(
            "asterisk.conf",
            f"""[directories]
astetcdir={self.root}
astmoddir=/usr/lib/asterisk/modules
astvarlibdir=/var/lib/asterisk
astdatadir=/var/lib/asterisk
astdbdir={self.root}/state
astkeydir={self.root}/keys
astrundir={self.root}/run
astlogdir={self.root}/log
astspooldir={self.root}/spool
[options]
nocolor=yes
verbose=0
""",
        )
        self.write(
            "modules.conf",
            "[modules]\nautoload=yes\nnoload=chan_dongle.so\nnoload=res_http_post.so\nnoload=app_voicemail.so\n",
        )
        self.write("logger.conf", "[general]\n[logfiles]\nconsole=error\n")
        self.write(
            "http.conf",
            f"[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport={self.http_port}\n",
        )
        self.write(
            "ari.conf",
            f"[general]\nenabled=yes\npretty=no\n[cellgate]\ntype=user\nread_only=no\npassword={self.ari_password}\n",
        )
        stun = self.options.get("stun_server", "stun.l.google.com:19302")
        stun_line = f"stunaddr={config_value(stun)}\n" if stun else ""
        self.write(
            "rtp.conf",
            f"[general]\nrtpstart={int(self.options.get('rtp_start', 28000))}\nrtpend={int(self.options.get('rtp_end', 28200))}\nicesupport=yes\n{stun_line}",
        )
        self.write(
            "extensions.conf",
            """[cellgate-incoming]
exten => gate,1,Stasis(cellgate,incoming)
same => n,Hangup()
[cellgate-viewer]
exten => view,1,Stasis(cellgate,viewer,${PJSIP_HEADER(read,X-CellGate-Call)},${PJSIP_HEADER(read,X-CellGate-Session)})
same => n,Hangup()
""",
        )
        external = self.options.get("external_address", "")
        nat = (
            f"external_signaling_address={config_value(external)}\nexternal_media_address={config_value(external)}\n"
            if external
            else ""
        )
        self.write(
            "pjsip.conf",
            f"""[global]
type=global
user_agent=CellGate-Intercom
endpoint_identifier_order=ip,username
[udp]
type=transport
protocol=udp
bind=0.0.0.0:{self.sip_port}
{nat}
[ws]
type=transport
protocol=ws
bind=127.0.0.1
[ha-viewer]
type=endpoint
transport=ws
context=cellgate-viewer
disallow=all
allow=opus,ulaw,alaw,g722,h264,vp8
webrtc=yes
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
auth=viewer-auth
[viewer-auth]
type=auth
auth_type=userpass
username=ha-viewer
password={self.sip_password}
#include provider.conf
""",
        )
        # Never reuse cloud credentials left by a previous process.
        self.write("provider.conf", "")

    def provider(self, account, peer):
        from urllib.parse import quote

        host, _ = host_port(account["host"])
        user = quote(account["username"], safe="-_.!~*'()")
        return f"""[cellgate-registration]
type=registration
transport=udp
outbound_auth=cellgate-auth
server_uri=sip:{config_value(account["host"].removeprefix("sip:"))}
client_uri=sip:{user}@{host}
contact_user=gate
expiration=60
retry_interval=30
fatal_retry_interval=0
forbidden_retry_interval=0
max_retries=3
outbound_proxy=sip:{peer[0]}:{peer[1]}\\;lr
[cellgate-auth]
type=auth
auth_type=userpass
username={config_value(account["username"])}
password={config_value(account["password"])}
[provider]
type=endpoint
transport=udp
context=cellgate-incoming
disallow=all
allow=ulaw,alaw,g722,opus,h264,vp8
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
media_encryption=sdes
media_encryption_optimistic=yes
aors=provider-aor
[provider-aor]
type=aor
contact=sip:{peer[0]}:{peer[1]}
qualify_frequency=25
qualify_timeout=3
[provider-identify]
type=identify
endpoint=provider
match={peer[0]}
"""

    async def cli(self, command):
        process = await asyncio.create_subprocess_exec(
            "/usr/sbin/asterisk",
            "-C",
            str(self.root / "asterisk.conf"),
            "-rx",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(8):
            data, _ = await process.communicate()
        text = data.decode(errors="replace")
        if (
            process.returncode
            or "No such command" in text
            or "Unable to reload" in text
        ):
            raise BackendError("Asterisk control unavailable")
        return text
