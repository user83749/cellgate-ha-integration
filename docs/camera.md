# CellGate camera and two-way talk in Home Assistant

## Requested behavior

- A normal Home Assistant gate-trigger button.
- Live camera video and two-way talk during a real intercom call.
- No take-picture button or stored-picture camera substitute.
- Mobile API only; web-player investigation is deferred.
- Do not access the owner's Home Assistant instance at this stage.

## Verified so far

Authorized mobile login, discovery, and SIP-information requests succeeded on November 2, 2026. The account has a Watchman EVO, one Gate port, and one Camera port. SIP credentials, server information, codec configuration, and DTMF configuration were returned while no call was active. The SIP-information response agrees with the account object already included in login. The integration parses this configuration and caches it privately in memory. See [SIP provisioning](sip-provisioning.md).

Android 4.0.61 static analysis identifies Linphone and SIP account configuration using UDP, including incoming-call acceptance with video enabled. The iOS binary has not been analyzed, and equal implementation across platforms is not assumed.

## Not implemented or tested

A separate diagnostic client successfully registered and removed its own contact. No call has been initiated, accepted, or recorded. No audio/video connection or physical gate command was sent during this validation. The integration does not yet expose a camera entity or two-way talk.

Next work is to implement the SIP account/registration lifecycle, establish concurrent-client behavior, receive and negotiate an incoming call, and route negotiated media into Home Assistant with an audio return path. Codec configuration contains numeric IDs; dynamic payload IDs are not sufficient to identify codecs without SDP or other protocol evidence.

According to the owner, the feed ends when the call ends or the gate is opened. That lifecycle must be validated and reflected by the camera entity rather than presenting an always-on feed.

Home Assistant announced camera two-way audio as a preview feature in Core 2026.11; an actual audio return transport is required before advertising the capability. [Camera two-way audio announcement](https://contributors.home-assistant.io/blog/2026/10/24/camera-two-way-audio/).

## Registration progress

The later [SIP diagnostic](sip-registration.md) authenticated and received 200 OK, then received 200 OK removing only its own contact. Production call/media handling is still pending. [Home Assistant runtime tests](home-assistant.md) now cover the gate integration lifecycle using synthetic data.
