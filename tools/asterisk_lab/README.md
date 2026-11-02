# Isolated Asterisk evaluation

This is a development lab, not a Home Assistant component or an installation script. It uses synthetic SIP credentials and a simulated intercom. It never reads CellGate account files, contacts CellGate, accesses Home Assistant, or triggers a physical gate.

Run on Linux with Docker and Python 3.14, using the pinned dependencies in `.github/workflows/evaluate-asterisk.yaml`:

```sh
docker pull ghcr.io/tech7fox/asterisk-hass-addon:6.4.0
python -u tools/asterisk_lab/run.py
```

The image is started directly as Asterisk with isolated lab configuration, bypassing Supervisor startup and production add-on templates. Ports 15060–15062 (SIP), 18088 (local SIP WebSocket), and 28000–28200 (RTP) must be free. The test needs Docker host networking. SIP/WebSocket signaling is bound to loopback; WebRTC uses local host ICE candidates, with no STUN/TURN server. This does not model Internet NAT/firewalls.

The cases exercise:

1. Stock outbound registration receiving a `302` next-hop redirect.
2. Registration with a pre-resolved loose-route outbound proxy, MD5 digest authentication, and the original identity/Request-URI.
3. An incoming provider INVITE routed to a registered synthetic SIP-over-WebSocket/WebRTC endpoint.
4. Real H.264 frames and PCMU audio delivered through Asterisk and decoded at the WebRTC endpoint.
5. Browser-side Opus microphone audio transcoded by Asterisk and decoded as PCMU at the simulated caller. The return signal must match the microphone's 900 Hz tone, distinct from the incoming 400 Hz tone.
6. Media connection closure without a SIP BYE: both SIP legs must remain Up for the bounded observation interval.
7. Remote BYE propagated to the WebSocket SIP endpoint.

`asterisk-lab-results/report.json` records the image digest, engine version and assertions. SDP, channel listings and engine logs are retained as CI artifacts. All accounts in those files are synthetic. A failing assertion fails the workflow; inability to follow the initial redirect is recorded as an expected compatibility finding rather than a workflow failure.

This does not implement redirect discovery/refresh, a native HA camera adapter, viewer reopen handling, or actual CellGate call interoperability. The WebRTC client is aiortc, not a real browser/HA frontend. Closing its media connection is a backend lifetime check, not proof of the complete requested HA viewer lifecycle. The production custom integration is unchanged.
