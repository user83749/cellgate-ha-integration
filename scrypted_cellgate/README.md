# CellGate Intercom for Scrypted — experimental

A Scrypted plugin that connects the existing CellGate Intercom Asterisk backend to Scrypted's WebRTC extension. It creates one persistent Doorbell device with call video, incoming audio, microphone-return signaling and a doorbell-press event. It does not replace the Home Assistant integration or sign into CellGate again.

## Requirements

- Working CellGate HA integration with intercom enabled and CellGate Intercom backend 0.4.4 or later. HA remains responsible for mobile authentication, provisioning and SIP registration.
- Scrypted with the WebRTC plugin installed. Enable WebRTC on this new CellGate device yourself. Enable HomeKit on the same device to pair it with Apple Home.
- An incoming intercom call. The plugin cannot create a monitoring session or access the camera between calls.

The standard HA 2026.11 microphone/Labs requirement does not apply to Scrypted's talk control. Scrypted's WebRTC extension manages microphone packets on the same call leg. Actual CellGate media, network routing and HomeKit behavior still need a live test.

## Install without npm publication

This package is not published to npm. Use Scrypted's official SDK deployment workflow from a development computer with Node.js 20 or newer (Node 24 is tested):

```sh
git clone https://github.com/user83749/cellgate-ha-integration.git
cd cellgate-ha-integration/scrypted_cellgate
npm ci --ignore-scripts
npm run build
npx scrypted login SCRYPTED_IP:10443
npm run scrypted-deploy -- SCRYPTED_IP:10443
```

Replace `SCRYPTED_IP:10443` with the actual direct Scrypted management address and port. The SDK login uses your Scrypted credentials. The build produces `out/plugin.zip`; deployment installs that bundle under `scrypted-cellgate-intercom`. Do not enter the package name in the npm plugin search: it has not been published there. Do not expose a new management port to the public internet for deployment.

A Home Assistant sidebar URL such as `http://192.168.2.196:8123/09e60fb6_scrypted` is an HA ingress page, not a direct Scrypted SDK deployment address. The SDK cannot use the HA sidebar URL or HA login credentials. If Scrypted's direct management listener is unavailable, installation requires an authenticated ingress deployment method or enabling its management port locally; do not substitute HA's port 8123.

Scrypted's development instructions: https://developer.scrypted.app/plugins.html

## Configure

Open **CellGate Intercom** under Scrypted's installed plugins and set:

| Setting | Value |
| --- | --- |
| CellGate Intercom Backend URL | The backend's LAN URL, e.g. `http://192.168.2.196:28190`. This must be reachable from Scrypted. It is neither the HA dashboard nor the DuckDNS frontend URL. |
| Backend API Token | The same independent `api_token` in the CellGate Intercom app. This is not an HA token. |
| CellGate Account Email | The same CellGate login email configured in HA. No CellGate password is needed. |

The plugin identifies the backend before sending the token. Incorrect backend authentication stops retries until settings change. The account email is used locally to derive the existing backend account ID; it is not used for another CellGate login.

Open the new **CellGate Intercom** doorbell device and enable the **WebRTC** extension. This supplies the VideoCamera and Intercom interfaces; the adapter implements native RTCSignalingChannel. Then enable **HomeKit** and pair the doorbell with Apple Home using Scrypted's normal flow.

Disable background prebuffering/continuous recording for this call-only device. Do not open its HA camera and Scrypted camera simultaneously: the backend currently permits one viewing media leg. Test the camera in Scrypted during a real incoming call before trying Apple Home. A static preview between calls is not provided; no snapshot control is added.

## Behavior

- Rings once for each incoming call, including if Scrypted attaches while that call is ringing. Event-stream reconnects do not repeat the doorbell press during the same plugin process.
- Always uses the same device/native ID across calls and restarts.
- Video/audio negotiation requests receive-only video and two-way audio. Scrypted's WebRTC extension sends microphone RTP only while its intercom forwarder is running. The backend requires no vendor StartTalk request.
- Closing a viewer deletes only its backend viewing session. CellGate's provider call continues and can be reopened. Provider call end stops the backend's media; there is no locally invented call deadline.
- Settings changes disconnect only this plugin's viewer/event connection. There is no account PUT/DELETE, gate actuation, call initiation, or second SIP registration in this plugin.
- The plugin needs no separate camera RTSP URL. The WebRTC extension handles the conversion for consumers that require a conventional video stream.

## Development checks

```sh
npm run typecheck
npm test
npm run build
npm run test:bundle
```

The SDK build uses its supported Rollup mode, and a separate bundle-startup test checks the compiled plugin against SDK runtime globals.

Tests cover service identification before credentials, bad-auth retry blocking, early ICE, call-end/reservation races, failed-offer cleanup, viewer close/reopen, and ring deduplication. The repository's Asterisk workflow can run the actual adapter against the packaged backend with synthetic SIP/RTP/WebRTC traffic. That lab uses an SDK-shaped signaling peer with aiortc and does not prove installed Scrypted WebRTC/HomeKit integration or a real CellGate call.
