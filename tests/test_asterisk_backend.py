"""Backend lifecycle and native HA signaling regressions, using synthetic data."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cellgate_intercom.bridge.config import BackendError, config_value
from cellgate_intercom.bridge.server import Bridge, complete_offer
from custom_components.cellgate.intercom import (
    AsteriskCallManager,
    IntercomAuthError,
    IntercomCall,
    IntercomError,
    backend_url,
)

SDP = "\r\n".join(
    [
        "v=0",
        "o=- 1 1 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 99 100",
        "a=mid:0",
        "a=ice-ufrag:original",
        "a=ice-pwd:original-password",
        "a=fingerprint:sha-256 original-fingerprint",
        "a=setup:actpass",
        "a=rtpmap:96 VP8/90000",
        "a=rtpmap:99 H264/90000",
        "a=fmtp:99 packetization-mode=1;profile-level-id=42e01f",
        "a=rtpmap:100 rtx/90000",
        "a=fmtp:100 apt=96",
        "a=candidate:1 1 udp 1 192.0.2.1 12345 typ host",
        "a=end-of-candidates",
        "",
    ]
)


def test_codec_filter_preserves_browser_ice_dtls_and_h264_format():
    result = complete_offer(SDP, [], {"h264"})
    assert "m=video 9 UDP/TLS/RTP/SAVPF 99\r\n" in result
    assert "VP8" not in result and "rtx" not in result
    for line in SDP.splitlines():
        if any(
            key in line
            for key in (
                "ice-ufrag",
                "ice-pwd",
                "fingerprint",
                "setup",
                "fmtp:99",
                "candidate:",
            )
        ):
            assert line in result
    assert result.count("a=end-of-candidates") == 1
    with pytest.raises(BackendError):
        complete_offer(SDP, [], {"h263"})


def test_trickle_candidates_attached_to_correct_media_without_header_injection():
    original = SDP.replace("a=candidate:1 1 udp 1 192.0.2.1 12345 typ host\r\n", "")
    with pytest.raises(BackendError):
        complete_offer(original, [], {"h264"})
    result = complete_offer(
        original,
        [
            {"mid": "0", "candidate": "candidate:2 1 udp 1 192.0.2.2 12345 typ host"},
            {"mid": "0", "candidate": "candidate:bad\r\na=evil"},
        ],
        {"h264"},
    )
    assert "a=candidate:2 1 udp" in result and "a=evil" not in result


def test_configuration_does_not_allow_new_directives():
    for value in ("bad\n#include secret", "bad\rvalue", "[section]", "\x00"):
        with pytest.raises(BackendError):
            config_value(value)
    assert config_value("private;password") == r"private\;password"


def make_bridge():
    bridge = Bridge(
        SimpleNamespace(cli=AsyncMock(return_value=""), write=Mock()), "synthetic-token"
    )
    bridge.owner = "synthetic-account"
    bridge.ari = AsyncMock(return_value={"value": "h264"})
    return bridge


async def test_ringing_is_not_answered_busy_call_rejected_and_viewer_detach_does_not_hangup_caller():
    bridge = make_bridge()
    await bridge.event(
        {"type": "StasisStart", "args": ["incoming"], "channel": {"id": "caller"}}
    )
    assert bridge.call and not bridge.call["answered"] and bridge.ring_sequence == 1
    assert not any(
        call.args[1].endswith("/answer") for call in bridge.ari.await_args_list
    )
    await bridge.event(
        {
            "type": "StasisStart",
            "args": ["incoming"],
            "channel": {"id": "second-caller"},
        }
    )
    bridge.ari.assert_awaited_with("DELETE", "/channels/second-caller", reason="busy")
    bridge.prepare("a" * 32, bridge.call["id"], SDP)
    viewer = SimpleNamespace(channel=None, close=AsyncMock())
    bridge.sessions["a" * 32].viewer = viewer
    await bridge.event(
        {
            "type": "StasisStart",
            "args": ["viewer", bridge.call["id"], "a" * 32],
            "channel": {"id": "viewer-channel"},
        }
    )
    assert bridge.call["answered"] and viewer.channel == "viewer-channel"
    bridge.ari.reset_mock()
    await bridge.detach("a" * 32)
    bridge.ari.assert_awaited_once_with("DELETE", "/channels/viewer-channel")
    assert (
        bridge.call and bridge.call["channel"] == "caller" and bridge.call["answered"]
    )
    bridge.prepare("b" * 32, bridge.call["id"], SDP)  # same call can be reopened
    assert len(bridge.sessions) == 1


async def test_caller_end_cleans_viewers_and_bridge_without_hanging_up_another_call():
    bridge = make_bridge()
    bridge.call = {
        "id": "call",
        "channel": "caller",
        "answered": True,
        "video": {"h264"},
    }
    bridge.bridge_id = "bridge"
    bridge.prepare("a" * 32, "call", SDP)
    viewer = SimpleNamespace(channel="viewer", close=AsyncMock())
    bridge.sessions["a" * 32].viewer = viewer
    await bridge.event({"type": "StasisEnd", "channel": {"id": "caller"}})
    assert bridge.call is None and not bridge.sessions and bridge.bridge_id is None
    assert not any(
        call.args == ("DELETE", "/channels/caller")
        for call in bridge.ari.await_args_list
    )
    viewer.close.assert_awaited_once()


async def test_reservation_collects_candidates_before_offer_submission():
    bridge = make_bridge()
    bridge.call = {
        "id": "call",
        "channel": "caller",
        "answered": False,
        "video": {"h264"},
    }
    bridge.client = Mock()
    bridge.prepare("a" * 32, "call", SDP)
    with pytest.raises(BackendError):
        bridge.prepare("b" * 32, "call", SDP)
    with patch("cellgate_intercom.bridge.server.Viewer") as viewer:
        viewer.return_value.offer = AsyncMock(return_value="unchanged-asterisk-answer")
        answer = await bridge.offer("a" * 32)
        assert answer == "unchanged-asterisk-answer"
        assert (
            "a=fingerprint:sha-256 original-fingerprint"
            in viewer.return_value.offer.call_args.args[0]
        )


@pytest.mark.parametrize(
    "value",
    [
        "http://ha.local:8123",
        "http://user:password@backend:28190",
        "http://backend:28190/api",
        "https://backend/?token=secret",
        "file:///data",
    ],
)
def test_wrong_backend_urls_rejected_before_network_access(value):
    with pytest.raises(IntercomError):
        backend_url(value)


async def test_bad_backend_auth_stops_without_retries_or_shutdown_request():
    manager = AsteriskCallManager(Mock(), AsyncMock(), "synthetic-account")
    await manager.set_options(
        {
            "backend_url": "http://backend:28190",
            "backend_token": "synthetic-" + "x" * 32,
        }
    )
    with patch(
        "custom_components.cellgate.intercom.check_backend",
        AsyncMock(side_effect=IntercomAuthError("Rejected")),
    ) as check:
        await manager.start()
        await manager.worker
        assert manager.state == "authentication_failed"
        check.assert_awaited_once()
        manager.account_provider.assert_not_awaited()
        manager.request = AsyncMock()
        await manager.stop()
        manager.request.assert_not_awaited()


async def test_ha_offer_answer_passthrough_and_viewer_close_never_disables_account():
    manager = AsteriskCallManager(Mock(), AsyncMock(), "synthetic-account")
    manager.call = IntercomCall("synthetic-call")
    manager.request = AsyncMock(
        side_effect=[{}, {"answer": "original-asterisk-sdp"}, {}, {}]
    )
    answer = await manager.offer(SDP, "native-ha-session")
    assert answer == "original-asterisk-sdp"
    assert manager.request.await_args_list[0].kwargs["json"] == {
        "call_id": "synthetic-call",
        "sdp": SDP,
    }
    await manager.candidate("native-ha-session", "candidate:original", "0", 0)
    await manager.close_viewer("native-ha-session")
    assert manager.call.id == "synthetic-call"
    assert not any(
        call.args == ("DELETE", manager.path)
        for call in manager.request.await_args_list
    )
    count = manager.request.await_count
    await manager.candidate("native-ha-session", "candidate:late", "0", 0)
    assert manager.request.await_count == count


async def test_camera_close_while_ice_gathering_releases_waiter():
    manager = AsteriskCallManager(Mock(), AsyncMock(), "synthetic-account")
    manager.call = IntercomCall("call")
    reserved = asyncio.Event()
    release = asyncio.Event()

    async def request(method, path, **kwargs):
        if method == "PUT":
            reserved.set()
            await release.wait()
        return {"answer": "answer"} if method == "POST" else {}

    manager.request = AsyncMock(side_effect=request)
    task = asyncio.create_task(manager.offer(SDP, "viewer"))
    await reserved.wait()
    candidate = asyncio.create_task(manager.candidate("viewer", "candidate:x"))
    await manager.close_viewer("viewer")
    release.set()
    await candidate
    with pytest.raises(IntercomError):
        await task
    assert not manager.sessions


def test_max_bundle_shared_candidates_require_identical_ice_credentials():
    first = SDP.replace("a=group:BUNDLE 0", "a=group:BUNDLE 0 1")
    video = (
        SDP.split("m=video", 1)[1]
        .replace("a=mid:0", "a=mid:1")
        .replace("a=candidate:1 1 udp 1 192.0.2.1 12345 typ host\r\n", "")
    )
    offer = first + "m=video" + video
    result = complete_offer(offer, [], {"h264"})
    assert result.count("a=candidate:1 1 udp 1 192.0.2.1 12345 typ host") == 2
    with pytest.raises(BackendError):
        complete_offer(
            first
            + "m=video"
            + video.replace("a=ice-ufrag:original", "a=ice-ufrag:different"),
            [],
            {"h264"},
        )
