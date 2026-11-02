# Call and media implementation

Version 0.3.0 implements an opt-in SIP receiver and native Home Assistant WebRTC camera. This is an initial, bounded SIP profile, not a general-purpose PBX or a claim of complete SIP interoperability.

## Lifecycle

The account's camera entity and unique ID persist across calls. It becomes available when a usable incoming SIP offer arrives. The incoming-call event emits `ring` once for a new call; retransmitted INVITEs do not create new events.

The receiver maintains one SIP account and one incoming dialog. Registration uses digest authentication, follows the observed single public-IP registrar redirect, renews before expiry and retries transport failures with bounded backoff. Incorrect authentication suspends retries. Only the receiver's own unique Contact is deregistered; wildcard deregistration is never sent.

Opening the camera negotiates a WebRTC viewer, prepares the SIP RTP sockets and answers with SDP. It retransmits its successful answer until ACK, handles caller CANCEL before acceptance, and tears down on remote BYE. Ringing without an answer has a 60-second local timeout. Accepted calls have no arbitrary duration limit: CellGate controls their normal end. Config-entry unload/shutdown is an explicit local teardown exception.

Closing or losing a WebRTC viewer closes that peer connection and microphone only. A media relay keeps the source tracks alive, allowing the same camera to be reopened during the same SIP call. Silence is sent on the SIP audio return while no microphone is present. One HA viewer is supported at a time.

## Media path

```text
CellGate SIP INVITE/SDP -> SDP negotiation -> RTP/SRTP sockets
                                                |
                                      codec decode / frames
                                                |
                                    aiortc native WebRTC
                                                |
                                     HA camera / browser

Browser microphone -> WebRTC audio frames -> G.711/G.722 encode -> SIP RTP/SRTP
```

Receiving queues and frame queues are bounded. Decode, resampling and encode work runs in the executor. Video is decoded and re-encoded for WebRTC; CPU requirements depend on the actual gate resolution and the HA host. This is not zero-copy passthrough.

Initial supported formats:

| Area | Support |
| --- | --- |
| SIP signaling | IPv4/UDP; digest REGISTER; INVITE, ACK, CANCEL, BYE, OPTIONS |
| Video | H.264 packetization modes 0/1, including out-of-band SPS/PPS; VP8 |
| Audio | PCMU, PCMA, G.722; browser microphone is resampled for the SIP encoder |
| Plain media | RTP/AVP and RTP/AVPF |
| Encrypted media | RTP/SAVP(F) with SDES `AES_CM_128_HMAC_SHA1_80` and a simple inline key |
| Feedback | RTCP receiver reports and video keyframe requests |
| NAT | STUN mapping on the actual RTP/RTCP sockets; symmetric RTP port learning from the negotiated IP |
| Not implemented | SIP TCP/TLS, IPv6 SIP media, SIP ICE/DTLS, ZRTP, other SRTP profiles, H.263, TURN relaying, general re-INVITE/session-timer renegotiation |

Unsupported offers are rejected rather than silently downgrading encryption. Codec IDs from account provisioning are not interpreted as definitive negotiated codecs. No SIP keys, SDP, caller identities or network addresses are exposed in entity state or logged by this implementation.

## Home Assistant

The camera implements native asynchronous offer, ICE-candidate and session-close hooks. Its supported stream path is WebRTC. It does not use a fake RTSP URL, a stored JPEG loop or a Take Picture action. The capability introduced in Core 2026.11 is declared only when the current SIP offer supports transmitting audio back to the intercom. HA's own WebRTC/Labs/HTTPS checks govern the frontend microphone interface.

The gate uses `CoverEntity`, device class `gate`, and only `CoverEntityFeature.OPEN`. Its open action is the same source-backed mobile `MOMENTARY_OPEN` command used previously. `Gate Closed` / `Gate Open` labels were observed in the owner's authorized discovery response. The cover reports these labels, with unknown for unrecognized states. It never assumes that sending a command proves a physical position.

## Dependencies

Released aiortc 1.15.0 requires PyAV below 18, while Core 2026.10.4's stream component pins PyAV 19. Using that release would conflict with HA's media stack. The integration therefore pins the official aiortc source revision:

`87d83729b2a4528dbf97c5d892ae487581157c3b`

Its metadata allows `av>=14,<20`. This immutable source is installed by HA's supported direct-URL requirement mechanism, and hassfest accepts the manifest. No custom native Linphone installation is required. The isolated 2026.10.4 environment passes `uv pip check` with PyAV 19.0.0. PyPI provides Python 3.14 Linux wheels for PyAV 19 on x86-64 and ARM64. Switch to a compatible released aiortc package when one is published and tested.

The bridge uses aiortc codec, jitter-buffer and RTP helpers in addition to the public peer-connection API; the immutable pin bounds those implementation dependencies. Updating that revision requires repeating the media tests.

## Evidence and remaining live validation

- Authorized mobile login, discovery and SIP provisioning succeeded.
- The new asynchronous receiver registered, renewed its registration and deregistered its own Contact against the real server. It did not accept calls or operate the gate.
- Local end-to-end tests exchange actual H.264 RTP video, PCMU audio and WebRTC microphone audio. They verify viewer closure/reopen without ending the original call and teardown on the simulated gate's BYE.
- Tests cover SDP selection/rejection, SRTP in both directions, actual-socket STUN discovery, partial-open cleanup, cover state/actions, persistent camera identity, ring events, options reload and legacy-button retirement.
- Full integration tests use Core 2026.10.4. A separate real Core 2026.11.0b3 camera API/capability smoke check passes; the published 2026.10 test-helper package is not compatible with a full beta runtime suite.
- Hassfest passes with zero invalid integrations.

A real incoming intercom call must still establish actual codec/encryption choices, routing through CellGate's service, NAT behavior, call-end signaling and simultaneous operation with the iOS app. The current receiver accepts SIP requests from the resolved/provisioned registrar addresses; a service that originates calls from additional gateways may require an explicitly established source policy.

Primary references:

- https://www.rfc-editor.org/rfc/rfc3261
- https://www.rfc-editor.org/rfc/rfc8760
- https://aiortc.readthedocs.io/en/latest/api.html
- https://github.com/aiortc/aiortc/tree/87d83729b2a4528dbf97c5d892ae487581157c3b
- https://contributors.home-assistant.io/blog/2026/10/24/camera-two-way-audio/
- https://contributors.home-assistant.io/core/entities/cover/
