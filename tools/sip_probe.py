"""Bounded SIP/UDP registration diagnostic, not a call/media implementation.

Reads a private GetSipInformationForUser JSON response. Prints sanitized status
only. Registers one unique contact for 60 seconds, then removes that contact.
Never uses wildcard deregistration or sends INVITE. RFC 3261 / RFC 8760.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ipaddress
import os
import re
import secrets
import socket
import time
from pathlib import Path


class ProbeError(Exception):
    """Safe diagnostic failure; no remote values in error messages."""


def safe(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
        raise ProbeError("Invalid SIP configuration")
    return value


def quote(value):
    return '"' + safe(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_response(packet):
    if len(packet) > 65535:
        raise ProbeError("Oversized SIP response")
    text = packet.decode("utf-8", errors="replace")
    lines = text.split("\r\n\r\n", 1)[0].split("\r\n")
    match = re.fullmatch(r"SIP/2\.0 (\d{3})(?: .*)?", lines[0])
    if not match:
        return None
    headers = {}
    last = None
    for line in lines[1:]:
        if line[:1] in (" ", "\t") and last:
            headers[last][-1] += " " + line.strip()
            continue
        if ":" not in line:
            raise ProbeError("Malformed SIP response headers")
        key, value = line.split(":", 1)
        last = key.lower().strip()
        last = {"i": "call-id", "v": "via", "m": "contact"}.get(last, last)
        headers.setdefault(last, []).append(value.strip())
    return int(match[1]), headers


def challenge_fields(value):
    if not value.lower().startswith("digest "):
        raise ProbeError("Unsupported SIP authentication scheme")
    result = {}
    pattern = r'\s*([\w-]+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^,\s]+))\s*(?:,|$)'
    rest = value[7:]
    while rest:
        match = re.match(pattern, rest)
        if not match:
            raise ProbeError("Malformed SIP digest challenge")
        key, quoted, plain = match.groups()
        result[key.lower()] = (
            re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else plain
        )
        rest = rest[match.end() :]
    if not result.get("realm") or not result.get("nonce"):
        raise ProbeError("Incomplete SIP digest challenge")
    return result


def digest(username, password, uri, fields, nc=1, cnonce=None):
    algorithm = fields.get("algorithm", "MD5").upper()
    base = algorithm.removesuffix("-SESS")
    hashes = {"MD5": "md5", "SHA-256": "sha256", "SHA-512-256": "sha512_256"}
    if base not in hashes:
        raise ProbeError("Unsupported SIP digest algorithm")
    cnonce = cnonce or secrets.token_hex(16)

    def h(text):
        return hashlib.new(hashes[base], text.encode()).hexdigest()

    realm, nonce = fields["realm"], fields["nonce"]
    ha1 = h(f"{username}:{realm}:{password}")
    if algorithm.endswith("-SESS"):
        ha1 = h(f"{ha1}:{nonce}:{cnonce}")
    ha2 = h(f"REGISTER:{uri}")
    qops = [s.strip().lower() for s in fields.get("qop", "").split(",") if s.strip()]
    if qops and "auth" not in qops:
        raise ProbeError("Unsupported SIP digest quality of protection")
    count = f"{nc:08x}"
    response = (
        h(f"{ha1}:{nonce}:{count}:{cnonce}:auth:{ha2}")
        if qops
        else h(f"{ha1}:{nonce}:{ha2}")
    )
    values = [
        f"username={quote(username)}",
        f"realm={quote(realm)}",
        f"nonce={quote(nonce)}",
        f"uri={quote(uri)}",
        f"response={quote(response)}",
        f"algorithm={algorithm}",
    ]
    if qops:
        values += ["qop=auth", f"nc={count}", f"cnonce={quote(cnonce)}"]
    elif algorithm.endswith("-SESS"):
        values += [f"cnonce={quote(cnonce)}"]
    if "opaque" in fields:
        values += [f"opaque={quote(fields['opaque'])}"]
    return "Digest " + ", ".join(values)


class RegistrationProbe:
    def __init__(self, account):
        self.username = safe(account["username"])
        self.password = safe(account["password"])
        self.host = safe(account["host"])
        # This diagnostic handles the observed bare DNS host only. No redirects.
        if not re.fullmatch(r"[A-Za-z0-9.-]+", self.host):
            raise ProbeError("Unsupported SIP server address format")
        if not re.fullmatch(r"[A-Za-z0-9_.!~*\x27()+-]+", self.username):
            raise ProbeError("Unsupported SIP username format")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect((self.host, 5060))
        address, port = self.sock.getsockname()
        self.contact = (
            f"<sip:ha-probe-{secrets.token_hex(8)}@{address}:{port};transport=udp>"
        )
        self.sent_by = f"{address}:{port}"
        self.call_id = secrets.token_hex(20)
        self.tag = secrets.token_hex(10)
        self.seq = 0
        self.auth = {}
        self.auth_count = {}
        self.uri = "sip:" + self.host
        self.redirected = False

    def close(self):
        self.sock.close()

    def request(self, expires):
        self.seq += 1
        branch = "z9hG4bK" + secrets.token_hex(12)
        headers = [
            f"REGISTER {self.uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.sent_by};rport;branch={branch}",
            "Max-Forwards: 70",
            f"From: <sip:{self.username}@{self.host}>;tag={self.tag}",
            f"To: <sip:{self.username}@{self.host}>",
            f"Call-ID: {self.call_id}",
            f"CSeq: {self.seq} REGISTER",
            f"Contact: {self.contact};expires={expires}",
            f"Expires: {expires}",
            "User-Agent: CellGate-HA-Diagnostic/0.1",
        ]
        for name, fields in self.auth.items():
            self.auth_count[name] = self.auth_count.get(name, 0) + 1
            headers += [
                name
                + ": "
                + digest(
                    self.username,
                    self.password,
                    self.uri,
                    fields,
                    self.auth_count[name],
                )
            ]
        headers += ["Content-Length: 0", "", ""]
        packet = "\r\n".join(headers).encode()
        deadline = time.monotonic() + 8
        delay = 0.5
        while time.monotonic() < deadline:
            self.sock.send(packet)
            until = min(deadline, time.monotonic() + delay)
            while time.monotonic() < until:
                self.sock.settimeout(max(0.001, until - time.monotonic()))
                try:
                    parsed = parse_response(self.sock.recv(65535))
                except socket.timeout:
                    break
                if not parsed:
                    continue  # Do not answer or initiate calls.
                code, received = parsed
                if received.get("call-id") != [self.call_id] or received.get(
                    "cseq"
                ) != [f"{self.seq} REGISTER"]:
                    continue
                vias = received.get("via", [])
                if not vias or not re.search(
                    r"(?:^|;)\s*branch=" + re.escape(branch) + r"(?:;|$)", vias[0]
                ):
                    continue
                if code >= 200:
                    return code, received
            delay = min(delay * 2, 4)
        raise ProbeError("SIP server did not complete the registration transaction")

    def listen(self, seconds, capture_dir):
        """Record incoming signaling privately; do not accept calls or media.

        This diagnostic deliberately sends no INVITE responses, so it cannot
        establish a dialog. Retransmissions are stored once. A coordinated call
        is required; its absence does not establish that routing is broken.
        """
        capture_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if capture_dir.stat().st_mode & 0o077:
            raise ProbeError("Capture directory must be private (mode 0700)")
        deadline = time.monotonic() + seconds
        seen = set()
        count = 0
        while time.monotonic() < deadline:
            self.sock.settimeout(min(1, max(0.001, deadline - time.monotonic())))
            try:
                packet = self.sock.recv(65535)
            except socket.timeout:
                continue
            # SIP requests only; no remote text is printed. Protect SDP, caller
            # identity, network addresses, and possible media keying material.
            if not packet.startswith(b"INVITE "):
                continue
            fingerprint = hashlib.sha256(packet).digest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            fd = os.open(
                capture_dir / ("invite-" + secrets.token_hex(8) + ".sip"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(fd, "wb") as output:
                output.write(packet)
            count += 1
            print(
                json.dumps({"incoming_invite_captured": True, "call_answered": False}),
                flush=True,
            )
        return count

    def redirect(self, headers):
        # Observed registrar returns a bare IPv4 next hop. Keep the original
        # account identity and Request-URI; never copy authentication to a hop.
        contacts = headers.get("contact", [])
        if self.redirected or len(contacts) != 1:
            raise ProbeError("Unsupported SIP redirect")
        match = re.fullmatch(r"<?sip:([0-9.]+):(\d+)>?", contacts[0])
        if not match:
            raise ProbeError("Unsupported SIP redirect target")
        host, port = match.group(1), int(match.group(2))
        if not ipaddress.ip_address(host).is_global or not 1 <= port <= 65535:
            raise ProbeError("SIP redirect target is not a public endpoint")
        self.sock.connect((host, port))
        self.redirected = True
        self.auth.clear()
        self.auth_count.clear()

    def register(self, expires):
        for _ in range(4):
            code, headers = self.request(expires)
            if code == 302:
                self.redirect(headers)
                continue
            if code not in (401, 407):
                return code
            name, challenge = (
                ("Authorization", "www-authenticate")
                if code == 401
                else ("Proxy-Authorization", "proxy-authenticate")
            )
            choices = headers.get(challenge, [])
            if not choices:
                raise ProbeError("SIP authentication challenge missing")
            parsed = challenge_fields(choices[0])
            if name in self.auth and parsed.get("stale", "").lower() != "true":
                raise ProbeError("SIP credentials rejected")
            self.auth[name] = parsed
            self.auth_count[name] = 0
        raise ProbeError("SIP authentication did not complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response_file", type=Path)
    parser.add_argument(
        "--listen-seconds", type=int, default=0, choices=range(0, 46), metavar="0..45"
    )
    parser.add_argument("--capture-dir", type=Path)
    args = parser.parse_args()
    if args.listen_seconds and not args.capture_dir:
        parser.error("--capture-dir is required when listening")
    probe = None
    attempted = False
    try:
        response = json.loads(args.response_file.read_text())
        probe = RegistrationProbe(response["data"]["sipInfo"]["account"])
        attempted = True
        code = probe.register(60)
        print(
            json.dumps(
                {
                    "registration_status": code,
                    "authenticated": bool(probe.auth),
                    "redirect_followed": probe.redirected,
                    "calls_started": 0,
                }
            ),
            flush=True,
        )
        if code == 200 and args.listen_seconds:
            count = probe.listen(args.listen_seconds, args.capture_dir)
            print(
                json.dumps(
                    {
                        "incoming_invite_count": count,
                        "listen_seconds": args.listen_seconds,
                    }
                )
            )
    except (OSError, ValueError, KeyError, TypeError, ProbeError) as error:
        print(
            json.dumps(
                {
                    "result": "failed",
                    "reason": str(error)
                    if isinstance(error, ProbeError)
                    else type(error).__name__,
                }
            )
        )
    finally:
        if probe:
            try:
                if attempted:
                    code = probe.register(0)
                    print(
                        json.dumps(
                            {
                                "own_contact_removal_status": code,
                                "wildcard_removal": False,
                            }
                        )
                    )
            except (OSError, ValueError, ProbeError):
                print(
                    json.dumps(
                        {
                            "own_contact_removal_status": "unconfirmed",
                            "requested_contact_expiry_seconds": 60,
                        }
                    )
                )
            finally:
                probe.close()


if __name__ == "__main__":
    main()
