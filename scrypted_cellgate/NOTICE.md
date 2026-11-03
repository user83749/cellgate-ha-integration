# Implementation references

This adapter is an independent implementation of the existing CellGate backend API and Scrypted SDK interfaces. No Arlo credentials, endpoints, or proprietary device code are included.

References consulted:
- Scrypted plugin development: https://developer.scrypted.app/plugins.html
- RTCSignalingChannel / RTCSignalingSession SDK contracts.
- Official WebRTC extension source: https://github.com/koush/scrypted/tree/main/plugins/webrtc
- Arlo plugin's RTC session adapter as an architectural reference: https://github.com/scryptedapp/arlo

The npm lockfile pins third-party dependencies and the official SDK. Their licenses remain those distributed with the respective packages.
