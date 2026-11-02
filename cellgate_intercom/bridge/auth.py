"""Digest authentication for the local Asterisk WebSocket endpoint."""

import hashlib
import re
import secrets


class SipAuthError(Exception):
    """Safe SIP authentication error."""


def safe(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
        raise SipAuthError("Invalid SIP configuration")
    return value


def quote(value):
    return '"' + safe(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def challenge_fields(value):
    if not value.lower().startswith("digest "):
        raise SipAuthError("Unsupported SIP authentication scheme")
    result = {}
    pattern = r'\s*([\w-]+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^,\s]+))\s*(?:,|$)'
    rest = value[7:]
    while rest:
        match = re.match(pattern, rest)
        if not match:
            raise SipAuthError("Malformed SIP digest challenge")
        key, quoted, plain = match.groups()
        result[key.lower()] = (
            re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else plain
        )
        rest = rest[match.end() :]
    if not result.get("realm") or not result.get("nonce"):
        raise SipAuthError("Incomplete SIP digest challenge")
    return result


def digest(username, password, uri, fields, nc=1, cnonce=None, method="REGISTER"):
    algorithm = fields.get("algorithm", "MD5").upper()
    base = algorithm.removesuffix("-SESS")
    hashes = {"MD5": "md5", "SHA-256": "sha256", "SHA-512-256": "sha512_256"}
    if base not in hashes:
        raise SipAuthError("Unsupported SIP digest algorithm")
    cnonce = cnonce or secrets.token_hex(16)

    def h(text):
        return hashlib.new(hashes[base], text.encode()).hexdigest()

    realm, nonce = fields["realm"], fields["nonce"]
    ha1 = h(f"{username}:{realm}:{password}")
    if algorithm.endswith("-SESS"):
        ha1 = h(f"{ha1}:{nonce}:{cnonce}")
    ha2 = h(f"{method}:{uri}")
    qops = [s.strip().lower() for s in fields.get("qop", "").split(",") if s.strip()]
    if qops and "auth" not in qops:
        raise SipAuthError("Unsupported SIP digest quality of protection")
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
