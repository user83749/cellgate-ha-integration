# Implementation references

Independent implementation of CellGate's observed mobile login/SIP provisioning contracts and standard SIP/RTP/WebRTC protocols. The plugin owns its login and media runtime; no Home Assistant code is imported at runtime.

The SIP/media modules derive from this repository's earlier receiver implementation. The official Scrypted Python SDK, WebRTC extension, and Arlo Python plugin were consulted for SDK packaging and RTC session contracts. No Arlo credentials, endpoints, or proprietary device code are included.

- https://developer.scrypted.app/plugins.html
- https://github.com/koush/scrypted/tree/main/plugins/webrtc
- https://github.com/scryptedapp/arlo
- https://github.com/aiortc/aiortc

The npm lockfile pins the SDK/build dependencies. Python direct dependencies are pinned in src/requirements.txt. Third-party licenses remain those distributed with each package.
