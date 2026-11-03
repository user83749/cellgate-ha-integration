# CellGate Intercom for Scrypted — experimental standalone plugin

Version 0.2.0 signs directly into the **CellGate mobile API with your CellGate email and password**. It obtains SIP credentials, registers for incoming calls, and bridges SIP video/audio to Scrypted WebRTC inside the plugin's normal Python runtime. It does not require the Home Assistant integration, CellGate Intercom app, Asterisk, a backend URL, or an API token.

It creates one persistent Doorbell device. Its camera is online only while a CellGate call exists. Gate control remains in the separate HA integration; this plugin adds no gate or Take Picture controls.

## Configure and upgrade from 0.1.0

1. Update the existing `scrypted-cellgate-intercom` plugin to 0.2.0. The package name and child device native ID (`intercom`) are unchanged, preserving the existing device identity.
2. Open **Scrypted → Plugins → CellGate Intercom → Settings**. Enter **CellGate Email** and **CellGate Password**. The old backend fields are removed. The old email is migrated; an old API token is never treated as a CellGate password.
3. Disable experimental intercom in the HA CellGate integration and stop its CellGate Intercom app before using this standalone receiver. Keep the HA gate integration installed. This avoids two SIP receivers competing for calls on the same account.
4. On the CellGate doorbell device, enable your installed **WebRTC** extension. It supplies Scrypted's VideoCamera and Intercom interfaces. Enable **HomeKit** there if you want to pair through Scrypted.
5. Check Connection Status for **Registered with CellGate; waiting for a call**. During an actual incoming call, open the camera in Scrypted first, then test talk. Test Apple Home afterward.

Passwords are stored in Scrypted's plugin storage and entered in a masked field. Authentication rejection stops automatic retries until credentials change or the plugin reloads. This plugin does not authenticate to HA or use its HTTP camera endpoints. Neither a DuckDNS URL nor an internal HA address belongs in these settings.

## Install without publishing to npm

Use Scrypted's official SDK deployment workflow from a computer with Node.js 20+ (Node 24 tested):

```sh
git clone https://github.com/user83749/cellgate-ha-integration.git
cd cellgate-ha-integration/scrypted_cellgate
npm ci --ignore-scripts
npm run build
npx scrypted login SCRYPTED_IP:10443
npm run scrypted-deploy -- SCRYPTED_IP:10443
```

Use Scrypted's direct management listener, not HA's port 8123 or its sidebar ingress URL. When deploying from a terminal inside the Scrypted container, use `127.0.0.1:10443`. Use Scrypted's own login. Do not expose a management port publicly. The package is not published to npm; entering its name in the plugin search will not install it.

The release's prebuilt archive contains `package.json` and `out/plugin.zip`. Extract it and use the same SDK deployment command from that directory; no local Python media dependencies are needed for deployment. Scrypted installs the Python requirements on its server.

Official development guide: https://developer.scrypted.app/plugins.html

## Calls and media

- Opening the camera answers the incoming SIP call. Closing the viewer detaches only WebRTC; the same intercom call can be reopened. SIP CANCEL/BYE ends the call. CellGate decides its duration. A missing SIP answer acknowledgment still triggers normal protocol cleanup.
- A doorbell event pulses once per incoming call; repeated SIP INVITEs do not create extra presses.
- There is no camera monitoring or preview image between calls. Disable continuous recording and background prebuffering for this device.
- One viewer media connection is supported at a time; Scrypted's WebRTC extension can act as the gateway for other consumers.
- Talk is muted initially. Scrypted's talk control enables microphone return. Turning talk off drops queued microphone frames and stops forwarding speech. Closing or losing the viewer also mutes talk without hanging up the provider call.
- SDP determines codecs. Supported incoming video is H.264/VP8, and audio is PCMU/PCMA/G.722. H.263-only video is unsupported. The bridge decodes and re-encodes media using aiortc/PyAV; it is not a zero-copy passthrough and has no measured latency/quality advantage over Asterisk.
- The current SIP profile is IPv4 UDP with digest authentication, RTP or SDES-SRTP, and STUN mapping on the actual RTP/RTCP sockets. SIP TLS/TCP, TURN, and arbitrary NAT/firewall configurations are not supported. No extra HTTP server, backend token, or camera URL is exposed by this plugin.
- Standard HA microphone/Labs requirements do not apply to Scrypted's talk controls.

## Validation and limitations

The Python 3.12 test suite runs the real Scrypted Python SDK against a synthetic device manager and a simulated SIP gate. It verifies direct mobile request contracts, bounded auth retries, credential-field migration, stable device identity, ring deduplication, actual H.264/audio RTP through WebRTC, microphone RTP return, mute, viewer close/reopen, STUN socket reuse, and provider call cleanup. It does not prove a real CellGate incoming call or Apple Home two-way audio works on your network.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt pytest pytest-asyncio
npm ci --ignore-scripts
.venv/bin/python -m pytest tests -q
npm run build
.venv/bin/python tests/check_bundle.py
```

Python requirements are bundled in `src/requirements.txt` using Scrypted's official Python plugin build. No HA packages or companion app are imported.
