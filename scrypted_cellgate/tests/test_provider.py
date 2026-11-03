"""Run the real Scrypted Python SDK with a synthetic device manager."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import scrypted_sdk as sdk
from cellgate_plugin.provider import CellGateProvider, CellGateCamera
from cellgate_plugin.sip import SipError


class Storage(dict):
    def getItem(self, key):
        return self.get(key)
    def setItem(self, key, value):
        self[key] = value
    def removeItem(self, key):
        self.pop(key, None)


@pytest.fixture
async def provider(monkeypatch):
    stores = {None: Storage(account_email='synthetic@example.test', api_token='not-a-password', backend_url='http://invalid.test')}
    states = {}
    manager = SimpleNamespace(
        onDevicesChanged=AsyncMock(), onDeviceEvent=AsyncMock(),
        getDeviceStorage=lambda n: stores.setdefault(n, Storage()),
        getDeviceState=lambda n: states.setdefault(n, SimpleNamespace()),
    )
    monkeypatch.setattr(sdk, 'deviceManager', manager)
    monkeypatch.setattr(CellGateProvider, "print", lambda *args: None)
    monkeypatch.setattr(CellGateCamera, "print", lambda *args: None)
    result = CellGateProvider()
    await result.ready
    yield result
    await result.close()
    await asyncio.gather(*tuple(result.tasks), return_exceptions=True)


async def test_starts_without_backend_or_credentials_and_preserves_camera_identity(provider):
    settings = {s['key']: s for s in await provider.getSettings()}
    assert settings['username']['value'] == 'synthetic@example.test'
    assert settings['password']['value'] == ''
    assert settings['password']['type'] == 'password'
    assert 'api_token' not in provider.storage and 'backend_url' not in provider.storage
    assert not provider.manager and not provider.http
    assert await provider.getDevice('intercom') is provider.camera
    assert provider.camera.online is False
    devices = sdk.deviceManager.onDevicesChanged.call_args[0][0]['devices']
    assert devices[0]['nativeId'] == 'intercom'
    assert devices[0]['interfaces'] == ['RTCSignalingChannel', 'BinarySensor', 'Online']


async def test_credential_settings_create_own_mobile_and_sip_clients(provider, monkeypatch):
    start = AsyncMock()
    monkeypatch.setattr('cellgate_plugin.provider.CallManager.start', start)
    await provider.putSetting('password', ' password with spaces ')
    assert provider.mobile.password == ' password with spaces '
    assert provider.mobile.username == 'synthetic@example.test'
    assert provider.manager.account_provider.__self__ is provider.mobile
    start.assert_awaited_once()
    assert provider.http is not None


async def test_ring_pulse_deduplicates_and_camera_stays_present(provider):
    call = SimpleNamespace(answered=False)
    manager = SimpleNamespace(call=call, ring_sequence=1)
    camera = provider.camera
    camera.update(manager)
    pulse = camera.pulse
    assert camera.online and camera.binaryState
    camera.update(manager)
    assert camera.pulse is pulse
    camera.clear_ring()
    camera.update(manager)
    assert camera.binaryState is False
    manager.call = None
    camera.update(manager)
    assert not camera.online
    assert await provider.getDevice('intercom') is camera


async def test_scrypted_signaling_talk_and_viewer_detach_do_not_hang_up(provider):
    call = SimpleNamespace(answered=False)
    sessions = {}
    manager = SimpleNamespace(call=call, sessions=sessions, set_talk=AsyncMock())
    # set_talk is intentionally synchronous like the real CallManager.
    talk = []
    manager.set_talk = talk.append
    async def offer(sdp, sid):
        assert sdp == 'v=0 synthetic'
        sessions[sid] = True
        return 'v=0 synthetic answer'
    async def close(sid):
        sessions.pop(sid, None)
        talk.append(False)
    manager.offer, manager.close_viewer = offer, close
    provider.manager = manager
    session = SimpleNamespace(getOptions=AsyncMock(return_value={}),
        createLocalDescription=AsyncMock(return_value={'type': 'offer', 'sdp': 'v=0 synthetic'}),
        setRemoteDescription=AsyncMock())
    control = await provider.camera.startRTCSignalingSession(session)
    assert session.createLocalDescription.call_args[0][1]['audio']['direction'] == 'sendrecv'
    await control.setPlayback({'audio': True})
    await control.setPlayback({'audio': False})
    assert talk == [True, False]
    await control.endSession()
    await control.endSession()
    assert manager.call is call and not sessions
    await control.setPlayback({'audio': True})
    assert talk == [True, False, False]
    # A second viewer can reopen the SAME call.
    second = await provider.camera.startRTCSignalingSession(session)
    await second.endSession()
    provider.manager = None


async def test_signaling_failure_releases_viewer(provider):
    provider.manager = SimpleNamespace(call=SimpleNamespace(answered=False), sessions={}, close_viewer=AsyncMock())
    session = SimpleNamespace(getOptions=AsyncMock(return_value={'requiresOffer': True}))
    with pytest.raises(SipError, match='WebRTC extension'):
        await provider.camera.startRTCSignalingSession(session)
    assert provider.camera.active is None
    provider.manager.close_viewer.assert_awaited_once()
    provider.manager = None


async def test_idle_camera_request_logs_without_answering(provider, monkeypatch):
    events = []
    monkeypatch.setattr(provider, 'diagnostic', events.append)
    with pytest.raises(SipError, match='No incoming'):
        await provider.camera.startRTCSignalingSession(None)
    assert events == ['Camera requested without a ringing/active SIP call; no SIP answer sent']
    assert provider.camera.active is None
