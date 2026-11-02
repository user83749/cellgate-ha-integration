# CellGate for Home Assistant

Unofficial Home Assistant custom integration using CellGate's **mobile API**. Version **0.4.0** moves experimental intercom calls to a dedicated Asterisk backend. HA keeps the gate cover, one persistent native camera for video/audio/talk, and the incoming-call event.

## Gate

The gate is a standard `cover` with device class `gate`. **Open** sends the mobile `MOMENTARY_OPEN` command once. There are no close, stop, hold-open, position, or Take Picture actions. State follows the mobile service's reported labels; unrecognized labels produce `unknown`. A raw diagnostic state sensor is disabled by default.

Gate control needs only the custom integration. It does not depend on Asterisk.

## Install the integration

Install this repository through HACS as an Integration, or copy `custom_components/cellgate` to `/config/custom_components/cellgate`. Restart HA and add **CellGate** under **Settings → Devices & services**, signing in with your CellGate account.

### Install the intercom backend

For HA OS / a Supervisor installation:

1. In the add-on/app store, add this repository URL: `https://github.com/user83749/cellgate-ha-integration`.
2. Install **CellGate Intercom**. It builds a dedicated Asterisk image; the first build can take several minutes.
3. Set its `api_token` to an independent random secret of 32–128 visible ASCII characters. For example, `openssl rand -hex 32` generates an appropriate token. This is the companion service's token, not a Home Assistant token or password.
4. Start the service. Its log should say **CellGate intercom backend ready**.
5. In the CellGate integration's configuration options, enable intercom calls and enter `http://HOME_ASSISTANT_LAN_IP:28190` as the backend URL, plus that same token.

The backend URL is a server-to-server connection from HA. The browser receives media through WebRTC, using the existing native camera interface. Do not put the HA dashboard URL in the backend field. There is no additional dashboard card or HomeKit component to install.

The service needs its own UDP SIP/RTP ports and an internal HTTP signaling port. Defaults and networking details are in [the backend setup guide](cellgate_intercom/DOCS.md). If behind NAT, set `external_address` to the public IP/DNS hostname when required by your SIP/media routing; Google STUN is enabled for ICE by default. Restrictive networks may require additional routing or a TURN solution; TURN is not configured by this release.

HA Container users can run the same [companion Docker service](cellgate_intercom/DOCS.md#home-assistant-container) separately.

### Upgrading from 0.3.x

The old in-process SIP receiver is replaced. Update the integration and install/configure the companion service before expecting intercom calls. Existing enabled-call settings without a backend produce `configuration_required`; the integration does not silently start the old receiver. The gate continues to work.

The existing camera and event unique IDs are preserved. Options changes start/stop or reconnect the manager in place, preserving the camera object and tokens. The camera advertises no snapshot URL. The old receiver is archived under `tools/legacy_receiver` for historical regression tests and is excluded from the installable HA component. HA no longer installs aiortc/PyAV for this integration.

## Intercom behavior — experimental

- One persistent native HA WebRTC camera, unavailable to stream between calls. Video, audio and microphone return use the same camera entity.
- An incoming-call event entity with event type `ring` for automations.
- Opening the camera during ringing answers the intercom.
- Closing the viewer ends only that viewer's connection and microphone. The intercom call stays active; reopening the camera rejoins that same call.
- CellGate's call-end signal ends the call. Disabling/unloading call support also releases the call and deregisters the backend's own SIP contact. No arbitrary active-call duration is imposed.
- One account/backend and one HA viewer at a time. Simultaneous operation with the CellGate app remains unverified.

Asterisk handles provider registration/digest authentication, SIP call signaling, RTP/SRTP and WebRTC DTLS/ICE. The HA adapter forwards SDP/ICE without decoding or encoding media. Compatible H.264/VP8 video can be forwarded without re-encoding; matching formats/profiles are required because Asterisk does not transcode video. Audio can be converted between supported codecs, including PCMU/PCMA, G.722 and Opus.

The standard HA microphone control requires Core **2026.11**'s two-way-audio Labs preview and an HTTPS frontend. Core 2026.10 supports the camera stream but lacks that new standard talk control.

## Validation and limits

The shipped companion image and HA manager have an automated Linux/Docker end-to-end test using a simulated intercom and real SIP/RTP/WebRTC traffic. It checks H.264 decoding and unchanged encoded video payloads, incoming audio, a distinct microphone tone returned as PCMU, delayed answering, trickled ICE, viewer close/reopen on the same call, remote BYE/CANCEL, and contact-specific deregistration. The HA test suite checks native entity lifecycle, camera signaling, options, late ICE, and camera-authentication/IP-ban regressions. Hassfest validates the component.

**A real incoming CellGate call remains necessary to validate this device's codec/encryption, Internet NAT behavior, and coexistence with the mobile app.** The synthetic test is not a latency benchmark or proof of real-device interoperability. There is no RTSP/HLS URL, snapshot feature, or video transcoder fallback.

Account login credentials remain in HA config-entry storage. Only SIP provisioning credentials are sent to the authenticated companion service, whose runtime config is mode `0600` and is cleared on normal shutdown/restart. It has no Supervisor/HA API permissions, never logs into HA, and does not receive your CellGate account login password. SIP credentials and backend tokens are excluded from entity attributes and normal logs.

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

- [Current call/media architecture](docs/call-media.md)
- [Asterisk evaluation](docs/asterisk-review.md)
- [Home Assistant compatibility](docs/home-assistant.md)
- [Mobile routes](docs/mobile-routes.md)
