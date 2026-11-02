# Current call/media architecture — 0.4.0

The Home Assistant custom integration uses the CellGate mobile API for account/gate discovery and momentary gate commands. It retrieves SIP provisioning during login and sends only the SIP account to the authenticated CellGate Intercom companion.

The companion runs a dedicated Asterisk engine. ARI monitors incoming calls and owns the provider channel/bridge. A local authenticated SIP-over-WebSocket viewing leg negotiates the native HA viewer's SDP with Asterisk. HA forwards the returned SDP to the camera API. ICE candidates are collected before SIP negotiation because this local leg uses a complete SDP offer rather than SIP trickle-ICE INFO.

**Media goes between the viewing client and Asterisk.** No aiortc/PyAV pipeline runs inside the custom component. Compatible encoded video is forwarded by Asterisk; SDP filtering limits the viewing leg to video codecs offered on the provider channel. DTLS fingerprints, ICE credentials and codec profile parameters from the viewer are retained. The test captures received encoded H.264 RTP payloads and compares them to those sent by the simulated gate.

Asterisk handles audio codec conversion, RTP/SRTP and the WebRTC transport. The initial configured codec set is PCMU/PCMA/G.722/Opus and H.264/VP8. Compatible video formats/profiles are mandatory; there is no video transcoder fallback. Provider SIP is UDP/IPv4 for the current provisioning profile. SDES is configured optimistically to permit the previously supported plain RTP and SDES SRTP profiles; real CellGate negotiation remains unverified.

## Call lifetime

1. Provider INVITE enters the ARI app. The camera becomes available, a ring event is emitted, and the call remains unanswered.
2. Opening the HA camera reserves one viewing session. Candidates may arrive during offer setup; they attach to that reservation before the SIP offer is submitted.
3. A valid local viewing channel answers the provider call and joins the bridge. Browser video/audio and microphone return use this one camera session.
4. Closing the HA viewer deletes only its channel. The answered provider channel/bridge stay alive.
5. A new viewer can negotiate a fresh WebRTC connection and join the same intercom call.
6. Provider BYE/CANCEL releases all viewing legs and the bridge. Disable/unload also releases calls and deregisters the backend's own contact.

The camera unique ID, event unique ID, and camera object/tokens are preserved across intercom option changes. No snapshot URL is advertised. Late ICE for a removed HA session is ignored. No browser/HA password login or token exchange is performed by the companion.

One account/backend and one viewer are supported. There is no active-call duration timer. Provider timeout policy governs normal call duration. Backend/control failures can also terminate calls to release orphaned resources.

## Dependencies and validation

The installed HA component uses HA's aiohttp session and built-in native WebRTC camera interfaces; it has no third-party media requirements. The companion Docker image supplies Asterisk/PJSIP and aiohttp. Test-only aiortc/PyAV dependencies simulate the browser/intercom; the old 0.3.x receiver is archived under `tools/legacy_receiver` and excluded from the integration package.

The full packaged-backend test is `.github/workflows/asterisk-backend.yaml` and `tools/asterisk_lab/backend.py`. Unit and real HA lifecycle/authentication tests run through `pytest`; official hassfest validates the component. See [backend setup](../cellgate_intercom/DOCS.md).

Synthetic loopback/host-network tests do not validate a real device's codec/profile, Internet NAT, TURN, long-running calls, mobile-app coexistence, or ARM64 execution. No comparative latency benchmark has been run. A real incoming CellGate call is still required.
