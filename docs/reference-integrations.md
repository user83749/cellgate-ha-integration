# Reference integration comparison

Reviewed November 2, 2026, from repositories supplied by the owner. Neither project was installed, started, or supplied with account credentials.

## daniel-mcdonough/cellgate-homeassistant

Repository: https://github.com/daniel-mcdonough/cellgate-homeassistant

Reviewed commit: `2f72a80e84a350bd9be102719190b84c5e3e3083`.

- Uses the **mobile API**, `https://cellgate.zapopen.com/api/CellCam`.
- Documents header-based POST login (`AuthUsername`, `AuthPassword`, empty push tokens, compatibility AppVersion `17`, AppType `android`). Login response includes `isSuccess` and `token`.
- Documents header-based `GetMapLocationList` and form-based `DevicesList` and `GateCommand`.
- Documents the misspelled response key `ProertyLocaitonId`.
- Provides momentary gate buttons. Its README says it reversed the Android app, found Linphone, and does not plan SIP support.
- No live camera or two-way audio implementation was found.

This is a source-backed contract reference, not a runtime observation of the owner's account. Its compatibility AppVersion is distinct from the public Android version name `4.0.61`.

The new client independently implements these protocol facts. Source files were not copied from the reference repository. The route inventory marks which contracts come from this source and which remain only static literals in the Android artifact.

## chelin1974/CellGateHome

Repository: https://github.com/chelin1974/CellGateHome

Reviewed commit: `3928d5feab530fe37098d317d217c5d3da7dd965`.

- Uses the **web portal API**, including AccountKit login, RSA transport wrapping, and model-specific DeviceAction requests.
- Is a standalone Java bridge rather than a Home Assistant custom integration.
- Its camera output is the most recent stored still image, optionally repeated by FFmpeg; this is not an intercom live video session.
- Infers a doorbell event from activity-log image entries. It does not establish a real incoming-call event or SIP session.
- No Linphone/SIP call receiver or two-way talk implementation was found.

Its stored-picture behavior is outside the owner's requested scope and was not incorporated.

## Migration decision

Use the mobile API as the Home Assistant HTTP backend. Do not assume that a web account session carries mobile SIP credentials. Do not infer session concurrency from route names. Implement actual camera/talk only after mobile SIP provisioning, registration, and media behavior are understood.

The legacy web transport is preserved only as ignored local research. It is not imported or used by the active custom component.


## SIP and WebRTC reference review

These projects were reviewed as source references only, without installation or account access.

### arturzx/hass-addons — SIP Indoor Station

Add-on revision: `7be7d001f3355a533142b7d8b812a5249f0ef8b8`.
Underlying SIP implementation: `1f0fc77ff1d7c0adc6a78ffa9f5e1c5f3269dfdb`.
Companion HA integration: `526cc4b905c3239f4083dcf93be69a86c318a5e6`.

- This is a local SIP registrar/server: door stations register to it. CellGate's verified setup instead requires this integration to register as a client to the vendor's cloud service.
- Incoming INVITE produces ringing; a separate answer action accepts it. Closing the browser signaling peer closes that WebRTC peer, while SIP BYE/CANCEL drives call teardown. This supports our separation of viewing and call lifetimes.
- Its SDP answer explicitly rejects video with port zero; the GStreamer bridge handles PCMU/PCMA audio. It does not supply a ready-made CellGate video bridge.
- Its companion HA integration supplies call-state entities/actions and signaling proxy endpoints; the custom card handles viewing. It does not establish that our native HA camera entity should be replaced with a browser SIP client.
- It keeps the current INVITE response with the active call in addition to a completed-transaction cache. Comparing that behavior exposed our cache-eviction rejection bug, independently fixed and regression-tested in 0.3.2.
- It provides configurable STUN/TURN and relay policy. TURN remains a potential improvement if real remote-network testing demonstrates a need; it is not required by the code review alone.

Sources:
https://github.com/arturzx/hass-addons/tree/7be7d001f3355a533142b7d8b812a5249f0ef8b8/sip_indoor_station
https://github.com/arturzx/sip-indoor-station/tree/1f0fc77ff1d7c0adc6a78ffa9f5e1c5f3269dfdb
https://github.com/arturzx/sip-indoor-station-integration/tree/526cc4b905c3239f4083dcf93be69a86c318a5e6

### andreaswatch/sip-hass-card-min

Reviewed revision: `b880dba15b425409c520f4902b66cd3f9cf72ce7`.

- A browser-side JsSIP custom card connecting to a SIP-over-WebSocket server, commonly Asterisk. It is not a persistent HA server-side SIP receiver.
- It supports SIP audio/video, mute controls and ICE configuration. Dismissing its popup and explicitly terminating its SIP session are distinct operations.
- A compatible CellGate WebSocket registrar has not been established; our live-validated signaling transport is UDP.
- Its existing-camera option embeds an HA camera stream; that does not itself implement CellGate's camera transport.

Source: https://github.com/andreaswatch/sip-hass-card-min/tree/b880dba15b425409c520f4902b66cd3f9cf72ce7

No source files from these projects were copied. The tested change is an independent correction to our SIP response handling. Camera identity, viewer-independent call lifetime and the 0.3.1 authentication mitigation remain intact. Actual incoming CellGate media remains unvalidated.
