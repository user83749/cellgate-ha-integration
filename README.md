# CellGate for Home Assistant

Unofficial Home Assistant custom integration using CellGate's **mobile API**. Version **0.3.2** fixes duplicate SIP invitations after response-cache expiry and includes the 0.3.1 camera-authentication fixes. Gate cover and experimental SIP intercom video/talk are included.

## Gate

The gate is a standard `cover` entity with device class `gate`. Its **Open** action sends the mobile `MOMENTARY_OPEN` command once. It exposes no close, stop, hold-open or position actions. Open/closed state comes from the service's reported state labels; unrecognized labels produce `unknown`, rather than guessing from numbers or elapsed time. Cloud-reported state is not independent verification of physical position.

A raw reported-state sensor is available as a diagnostic entity, disabled by default. There is no Take Picture button.

## Intercom calls — experimental

The camera and incoming-call event entities stay registered even while call support is disabled. Enable **Intercom calls** in the integration's configuration options to start the SIP receiver:

- One persistent Home Assistant WebRTC camera entity for the account, reused for every call's video and audio. It remains registered but unavailable to stream between calls.
- An incoming-call event entity, with event type `ring`, for automations.
- Persistent SIP registration, renewal, reconnection and contact-specific deregistration on unload/shutdown.

Opening the camera while the intercom rings answers the SIP call. Closing the viewer releases its WebRTC connection and microphone; **it does not hang up the intercom call**. You can reopen the same camera during that call. CellGate's SIP call-end signal ends the session and releases its media resources. Disabling/unloading the integration also releases the call. No arbitrary active-call duration is imposed.

Supported initial media profile: SIP over IPv4/UDP; H.264 or VP8 video; PCMU/PCMA (G.711) or G.722 audio; RTP/AVP(F), or SDES SRTP with `AES_CM_128_HMAC_SHA1_80`. Formats are negotiated from the incoming SDP, not inferred from codec IDs in provisioning. SIP ICE/DTLS, other encryption profiles and video codecs such as H.263 are not implemented. One HA viewer is supported at a time. This is a native WebRTC camera; there is no reusable RTSP/HLS URL or stored-picture substitute.

Home Assistant **2026.11** introduces the standard camera talk interface as a Labs preview. Enable that preview and open HA over HTTPS to use its microphone control. The backend's audio return was tested with a real WebRTC microphone track. Camera API/capability checks also passed on Core 2026.11.0b3. Core 2026.10.4 has full integration lifecycle coverage, but does not provide that new standard talk control.

Changing the intercom option starts/stops its receiver in place, without recreating the camera or its tokens. The WebRTC-only camera does not advertise a tokenized snapshot URL.

Google STUN (`stun.l.google.com:19302`) is used for media address discovery. A TURN relay is not included, so restrictive NAT/firewall configurations may need further work. Concurrent behavior with the CellGate mobile app and the real incoming intercom media path remain unverified.

## Installation

1. Copy `custom_components/cellgate` into your HA configuration directory: `/config/custom_components/cellgate/manifest.json` must exist.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → CellGate**, then sign in.
4. For calls, open the integration's configuration options and enable **Intercom calls (experimental)**.
5. During an incoming intercom call, open its camera entity to answer and view it.

HA installs the integration's media dependencies automatically. The first installation requires access to PyPI and GitHub. The aiortc dependency is pinned to an immutable official upstream revision that supports HA's PyAV version; [dependency details](docs/call-media.md#dependencies).

### Upgrading from 0.2.2

The old trigger button is replaced with the gate cover. Its registry metadata is preserved and the old button is disabled by the integration. Update automations that referenced the button to use `cover.open_cover` on the new gate entity. Existing reported-state sensors remain available; fresh installations default that diagnostic sensor to disabled.

The integration uses no web-portal authentication or web fallback. Account credentials are stored in HA config-entry storage. SIP credentials stay in client memory and are excluded from entity attributes and logs. No account secrets, raw captures or private provisioning responses are included in the repository.

## Validation and current limits

Mobile login/discovery/SIP provisioning and the new receiver's registration, authenticated renewal and own-contact deregistration have succeeded against the owner's account. No gate command or live call acceptance was performed during those checks.

The test suite includes real local SIP/RTP/WebRTC transport with a simulated intercom: H.264 video, audio reception, microphone return, viewer reopen without ending the call, remote hangup, cancellation, STUN mapping, SRTP, and HA entity/options/unload behavior. All local call traffic uses this computer's interfaces; tests do not contact CellGate or operate the gate.

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

Hassfest validation passes. A real incoming CellGate call and installation on the owner's HA instance are still needed before treating call support as proven for this device.

- [Call/media implementation and supported profile](docs/call-media.md)
- [Home Assistant compatibility](docs/home-assistant.md)
- [Verified SIP provisioning](docs/sip-provisioning.md)
- [SIP registration evidence](docs/sip-registration.md)
- [Mobile routes](docs/mobile-routes.md)
- [Reference-project comparison and provenance](docs/reference-integrations.md)
