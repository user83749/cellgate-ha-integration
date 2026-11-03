"""The mobile API's read-only login and SIP provisioning contracts.

No portal, Home Assistant, gate command, or local backend API is used.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from dataclasses import dataclass

import aiohttp


class CellGateError(Exception):
    """Safe public protocol error, without server bodies or credentials."""


class CellGateAuthError(CellGateError):
    pass


@dataclass(frozen=True, repr=False)
class SipAccount:
    username: str
    password: str
    host: str


def sip_account(value):
    if not isinstance(value, dict):
        raise CellGateError('Mobile SIP account is missing')
    fields = [value.get(k) for k in ('username', 'password', 'host')]
    if any(not isinstance(v, str) or not v.strip() for v in fields):
        raise CellGateError('Mobile SIP provisioning is incomplete')
    if any('\r' in v or '\n' in v for v in fields):
        raise CellGateError('Invalid mobile SIP provisioning')
    return SipAccount(*fields)


def token_expiry(token):
    try:
        part = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))).get('exp')
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return None


class MobileClient:
    BASE = 'https://cellgate.zapopen.com/api/CellCam/'

    def __init__(self, session, username, password):
        self.session = session
        self.username = username
        self.password = password
        self.lock = asyncio.Lock()
        self.token = None
        self.expires = None
        self.account = None
        self.auth_rejected = False

    async def post(self, endpoint, headers):
        if endpoint not in ('Login', 'GetSipInformationForUser'):
            raise CellGateError('Unsupported mobile request')
        try:
            async with self.session.post(
                self.BASE + endpoint, headers={'Accept': 'application/json', **headers},
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise CellGateAuthError('CellGate rejected authentication')
                if not 200 <= response.status < 300:
                    raise CellGateError('CellGate mobile service is unavailable')
                # Bound provisioning responses; never keep or log the raw response.
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 1_048_576:
                        raise CellGateError('Mobile response exceeds the size limit')
                result = json.loads(raw)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise CellGateError('Unable to read the CellGate mobile service') from None
        if not isinstance(result, dict):
            raise CellGateError('Invalid mobile response')
        return result

    async def login(self):
        try:
            reply = await self.post('Login', {
                'AuthUsername': self.username, 'AuthPassword': self.password,
                'DeviceToken': '', 'VoipToken': '', 'AppVersion': '17', 'AppType': 'android',
            })
            if reply.get('isSuccess') is not True:
                raise CellGateAuthError('CellGate rejected mobile sign-in')
        except CellGateAuthError:
            self.auth_rejected = True
            raise
        token = reply.get('token')
        if not isinstance(token, str) or not token:
            raise CellGateError('Mobile sign-in did not return a token')
        self.token, self.expires, self.account = token, token_expiry(token), None
        user = reply.get('sipUser')
        if isinstance(user, dict) and user.get('IsSipAppUser') is True:
            details = user.get('ResponseDetails')
            if isinstance(details, dict) and details.get('account') is not None:
                self.account = sip_account(details['account'])

    async def get_sip_account(self):
        async with self.lock:
            if self.auth_rejected:
                raise CellGateAuthError('Correct the CellGate credentials in plugin settings')
            if not self.token or (self.expires is not None and time.time() >= self.expires - 30):
                await self.login()
            if self.account:
                return self.account
            for attempt in range(2):
                try:
                    reply = await self.post('GetSipInformationForUser', {
                        'Authorization': 'Bearer ' + self.token, 'AppVersion': '17',
                    })
                except CellGateAuthError:
                    if attempt:
                        self.auth_rejected = True
                        raise
                    await self.login()
                    if self.account:
                        return self.account
                    continue
                if reply.get('isSuccess') is not True:
                    raise CellGateError('CellGate rejected SIP provisioning')
                data = reply.get('data')
                if not isinstance(data, dict) or 'sipInfo' not in data:
                    raise CellGateError('Mobile SIP information is missing')
                if data['sipInfo'] is None:
                    return None
                self.account = sip_account(data['sipInfo'].get('account') if isinstance(data['sipInfo'], dict) else None)
                return self.account

    def clear(self):
        self.password = self.token = self.account = self.expires = None
