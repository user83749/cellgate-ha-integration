# 0.2.0

Replace the backend adapter with a standalone Scrypted Python plugin. Add CellGate email/password settings, mobile API login and SIP provisioning, direct SIP registration and incoming-call handling, and an in-process RTP/WebRTC bridge. Remove all backend URL/API-token settings and HA/Asterisk dependencies. Preserve the package/device identity; migrate only the old email.

Mute microphone return until talk is enabled; detach viewers without ending the CellGate call. Add direct authentication, SDK settings/signaling, and real synthetic RTP video/audio/talk regression tests. Real CellGate and HomeKit validation remains necessary.

# 0.1.0 — superseded

Initial adapter depended on the separate HA CellGate Intercom backend. It did not accept CellGate credentials or operate independently. Use 0.2.0 for standalone Scrypted operation.
