# SIP registration: verified, call media pending

On November 2, 2026 a bounded SIP/UDP diagnostic using the owner's mobile-provisioned credentials received:

1. `302` from the supplied SIP server, with a bare public IPv4/port Contact redirect.
2. A digest authentication challenge from the redirected registrar.
3. `200 OK` for the authenticated `REGISTER`.
4. `200 OK` when unregistering only the diagnostic client's unique Contact.

The account identity and registration Request-URI remained based on the provisioned SIP host. Authentication was computed for the challenged transaction after the redirect. No wildcard unregister, outgoing INVITE, call acceptance, media connection or gate command was sent. A successful registration does not prove incoming call routing, NAT/media reachability or simultaneous registration behavior with the iOS app.

## Reusable development diagnostic

`tools/sip_probe.py` uses only the Python standard library. It reads a private successful SIP-information response file; no credentials are supplied on the command line or copied into project fixtures. It logs status codes and boolean results only. Registration is requested for 60 seconds and cleanup targets its own unique contact. A server may apply its own expiry policy; cleanup status is reported explicitly.

```sh
python tools/sip_probe.py /private/path/sip-response.json
```

For a coordinated incoming-call capture, add:

```sh
python tools/sip_probe.py /private/path/sip-response.json \
  --listen-seconds 45 --capture-dir /private/path/call-capture
```

The capture directory must be mode 0700. Incoming INVITEs are written as mode-0600 files and never printed. They can contain private identities, network addresses and media keying information. Keep them outside the repository. The listener does not answer an incoming call or send media; it is a short diagnostic, not an unattended call receiver. Its absence of captured requests would not, on its own, prove that the account cannot receive calls.

## Next evidence needed

An incoming intercom call's INVITE/SDP will establish the offered audio/video codecs, transport, directions, encryption and network endpoints. A production media backend must then negotiate the call, handle cancellation/end-of-call, exchange media and bridge to Home Assistant's camera/WebRTC interfaces. Those parts are not implemented by this probe.

The app's Linphone backend remains a useful implementation reference. Current official Python wheels support Linux x86-64 only, so adding that wheel as a universal Home Assistant requirement would exclude other architectures. No such dependency has been added.

References:

- https://www.rfc-editor.org/rfc/rfc3261
- https://www.rfc-editor.org/rfc/rfc8760
- https://wiki.linphone.org/xwiki/wiki/public/view/Lib/Getting%20started/Python/
