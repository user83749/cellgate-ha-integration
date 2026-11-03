"""Scrypted device provider. SIP/media live in this plugin, without an HTTP API."""
from __future__ import annotations

import asyncio
import secrets

import aiohttp
import scrypted_sdk as sdk
from scrypted_sdk import ScryptedDeviceBase

from .calls import CallManager
from .sip import SipError
from .mobile import MobileClient

NATIVE_ID = 'intercom'


class ViewerControl:
    def __init__(self, camera, manager, call):
        self.camera, self.manager, self.call = camera, manager, call
        self.sid = secrets.token_hex(16)
        self.closed = False
        self.started = False

    async def getRefreshAt(self):
        return None

    async def extendSession(self):
        # CellGate owns the call duration; a viewer cannot extend that call.
        return None

    async def setPlayback(self, options):
        if not self.closed and self.manager.call is self.call and self.sid in self.manager.sessions:
            self.manager.set_talk(isinstance(options, dict) and options.get('audio') is True)

    async def endSession(self):
        if self.closed:
            return
        self.closed = True
        await self.manager.close_viewer(self.sid)
        if self.camera.active is self:
            self.camera.active = None


class CellGateCamera(ScryptedDeviceBase):
    def __init__(self, provider):
        super().__init__(NATIVE_ID)
        self.provider = provider
        self.active = None
        self.pulse = None
        self.last_ring = None
        self.online = False
        self.binaryState = False

    def update(self, manager):
        call = manager.call if manager else None
        self.online = bool(call)
        if self.active and (not manager or self.active.call is not call or
                            (self.active.started and self.active.sid not in manager.sessions)):
            self.provider.spawn(self.active.endSession())
        key = (id(manager), manager.ring_sequence) if manager else None
        if call and not call.answered and self.last_ring != key:
            self.last_ring = key
            self.binaryState = False
            self.binaryState = True
            if self.pulse:
                self.pulse.cancel()
            self.pulse = asyncio.get_running_loop().call_later(1, self.clear_ring)
        if not call:
            self.clear_ring()

    def clear_ring(self):
        self.binaryState = False
        if self.pulse:
            self.pulse.cancel()
            self.pulse = None

    async def disconnect(self):
        self.clear_ring()
        self.online = False
        if self.active:
            await self.active.endSession()

    async def startRTCSignalingSession(self, session):
        manager = self.provider.manager
        if not manager or not manager.call:
            raise SipError('No incoming CellGate call is available')
        if self.active:
            raise SipError('This CellGate call already has a viewer')
        control = ViewerControl(self, manager, manager.call)
        self.active = control
        setup = {'type': 'offer', 'audio': {'direction': 'sendrecv'}, 'video': {'direction': 'recvonly'}}
        try:
            options = await session.getOptions() or {}
            if options.get('requiresOffer'):
                raise SipError('Enable the Scrypted WebRTC extension for this viewer')
            # No candidate callback: Scrypted gathers candidates into the SDP.
            # This avoids losing trickled candidates across the Python RPC boundary.
            offer = options.get('offer') or await asyncio.wait_for(
                session.createLocalDescription('offer', setup), timeout=45,
            )
            if not isinstance(offer, dict) or offer.get('type') != 'offer' or not isinstance(offer.get('sdp'), str):
                raise SipError('Viewer returned an invalid WebRTC offer')
            if control.closed or manager.call is not control.call:
                raise SipError('CellGate call ended while connecting')
            answer = await manager.offer(offer['sdp'], control.sid)
            control.started = True
            if control.closed or manager.call is not control.call:
                raise SipError('CellGate call ended while connecting')
            await session.setRemoteDescription({'type': 'answer', 'sdp': answer}, setup)
            return control
        except BaseException as error:
            await control.endSession()
            if isinstance(error, (asyncio.CancelledError, SipError)):
                raise
            raise SipError("Unable to connect the CellGate WebRTC viewer") from None


class CellGateProvider(ScryptedDeviceBase):
    def __init__(self):
        super().__init__()
        self.camera = None
        self.manager = None
        self.http = None
        self.mobile = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.status = 'Enter your CellGate email and password'
        # Migrate the harmless email only. An old API token is NEVER a password.
        if not self.storage.getItem('username') and self.storage.getItem('account_email'):
            self.storage.setItem('username', self.storage.getItem('account_email'))
        for key in ('backend_url', 'api_token', 'account_email'):
            self.storage.removeItem(key)
        self.ready = self.spawn(self.initialize())

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        def finished(t):
            if not t.cancelled() and t.exception():
                # Do not log library exceptions that can contain SDP or auth data.
                self.status = 'CellGate task failed; reload the plugin to retry'
                self.print(self.status)
        task.add_done_callback(finished)
        return task

    async def initialize(self):
        await sdk.deviceManager.onDevicesChanged({'devices': [{
            'nativeId': NATIVE_ID, 'name': 'CellGate Intercom', 'type': 'Doorbell',
            'interfaces': ['RTCSignalingChannel', 'BinarySensor', 'Online'],
            'info': {'manufacturer': 'CellGate', 'model': 'Call camera'},
        }]})
        self.camera = CellGateCamera(self)
        await self.restart()

    async def getDevice(self, nativeId):
        await self.ready
        if nativeId != NATIVE_ID:
            raise ValueError('Unknown CellGate device')
        return self.camera

    async def releaseDevice(self, id, nativeId):
        if nativeId == NATIVE_ID:
            await self.close()

    async def getSettings(self):
        return [
            {'key': 'username', 'title': 'CellGate Email', 'value': self.storage.getItem('username') or '',
             'description': 'Sign in directly to your CellGate mobile account.'},
            {'key': 'password', 'title': 'CellGate Password', 'type': 'password',
             'value': self.storage.getItem('password') or '',
             'description': 'Your CellGate account password. Stored in Scrypted plugin storage.'},
            {'key': 'status', 'title': 'Connection Status', 'readonly': True, 'value': self.status},
            {'key': 'call_state', 'title': 'Call State', 'readonly': True,
             'value': self.manager.state if self.manager else 'disabled'},
            {'key': 'media', 'title': 'Call Media', 'readonly': True, 'value': self.media_status()},
        ]

    def media_status(self):
        if not self.manager or not self.manager.call:
            return 'Available only during an incoming call'
        return ', '.join(f'{c.spec.codec.mimeType}: {c.packets_received} received / {c.packets_sent} sent'
                         for c in self.manager.call.bridge.channels)

    async def putSetting(self, key, value):
        if key not in ('username', 'password') or not isinstance(value, str):
            raise ValueError('Invalid CellGate setting')
        await self.ready
        # Password whitespace is meaningful.
        self.storage.setItem(key, value.strip() if key == 'username' else value)
        await self.restart()
        await self.onDeviceEvent('Settings', None)

    def changed(self):
        manager = self.manager
        if not manager:
            return
        states = {
            'idle': 'Registered with CellGate; waiting for a call',
            'active': 'Intercom call answered', 'ringing': 'Incoming intercom call',
            'connecting': 'Connecting directly to CellGate', 'reconnecting': 'Reconnecting to CellGate',
            'authentication_failed': 'CellGate authentication rejected. Correct credentials, then reload the plugin.',
            'unsupported': 'This CellGate account has no SIP configuration',
        }
        status = states.get(manager.state, manager.last_error or manager.state)
        if status != self.status:
            self.status = status
            self.spawn(self.onDeviceEvent("Settings", None))
        self.camera.update(manager)

    async def restart(self):
        async with self.lock:
            await self._close()
            username = self.storage.getItem('username') or ''
            password = self.storage.getItem('password') or ''
            if not username or not password:
                self.status = 'Enter your CellGate email and password'
                return
            self.http = aiohttp.ClientSession()
            self.mobile = MobileClient(self.http, username, password)
            self.manager = CallManager(self.mobile.get_sip_account, stun_server=('stun.l.google.com', 19302))
            self.manager.subscribe(self.changed)
            await self.manager.start()

    async def _close(self):
        if self.camera:
            await self.camera.disconnect()
        if self.manager:
            await self.manager.stop()
            self.manager = None
        if self.http:
            await self.http.close()
            self.http = None
        if self.mobile:
            self.mobile.clear()
            self.mobile = None

    async def close(self):
        async with self.lock:
            await self._close()
