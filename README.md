# CellGate for Home Assistant

Unofficial Home Assistant custom integration using CellGate's **mobile API** at `cellgate.zapopen.com`.

**Development build, version 0.2.2.** The mobile client replaces the earlier web-portal prototype. There is no web-portal authentication or automatic fallback in the active integration. Mobile login, location/device discovery, and SIP provisioning have succeeded against the owner's account. Gate commands have not been sent during validation. Setup, configuration flows, entities, availability and unloading pass tests against Home Assistant 2026.10.4 using synthetic API data. Official hassfest validation also passes with zero invalid integrations. Installation on the owner's instance remains untested.

## Implemented

- Sign in through the Home Assistant configuration UI using the mobile API.
- Discover locations, devices, and explicitly identified gate/door ports.
- A **Trigger gate** button sends the mobile `MOMENTARY_OPEN` command once.
- A reported-state sensor uses the server-provided state label, with the raw mobile `lastState` as a fallback.
- Obtain SIP account configuration during mobile login and support a dedicated authenticated SIP-information refresh. Configuration stays in client memory and is not exposed in entity attributes. This does not register a SIP client.
- Poll every 60 seconds. Recover an unauthorized read once; never replay gate commands.
- Stop automatic login attempts after rejected credentials; use Home Assistant reauthentication to correct them.
- Camera ports, picture commands, hold-open commands, and close commands are not exposed.

## Camera and two-way audio

These are requested but **not yet implemented**. Android artifact analysis identified native Linphone SIP/UDP call handling and the mobile SIP-provisioning endpoint names. SIP credentials and the server are now retrieved successfully, without an incoming call. A separate diagnostic successfully authenticated a short SIP/UDP registration and then removed its own contact. Production SIP call handling and media bridging still require implementation and validation. A reusable HTTP video URL has not been established.

The app's single-session versus multiple-session behavior is unverified. Existing route names such as `CheckUserAlreadyLogin`, `DeviceTokenUpdate`, and `RefreshSIPToken/` do not prove that simultaneous mobile clients are allowed or harmless. The current client does not register for push notifications or SIP calls.

## Installation

1. Copy `custom_components/cellgate` into your Home Assistant configuration directory so `/config/custom_components/cellgate/manifest.json` exists.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → CellGate**.
4. Enter the username and password for your CellGate account.
5. Confirm discovered devices and states before coordinating any physical gate test.

Home Assistant stores credentials in its config-entry storage. No account credentials are included in this project. The integration sends credentials only to the mobile API over HTTPS and does not follow redirects. Returned SIP credentials remain in client memory and are not included in entity states or attributes. Empty `DeviceToken` and `VoipToken` headers match the reference integration's gate-control login contract; their effects on concurrent app sessions have not been live-validated.

Once published, this repository can be added to HACS as a custom Integration repository. A previous development installation may acquire new entity identifiers after this mobile migration; do not assume old automations or registry entries migrate automatically.

## Evidence and references

- [Mobile route inventory](docs/mobile-routes.md): all identified route literals, with the five implemented contracts distinguished from untraced routes.
- [Mobile call investigation](docs/research-2026-11-02.md): Android call stack and capture findings.
- [Home Assistant compatibility](docs/home-assistant.md): runtime conventions and validation.
- [SIP registration](docs/sip-registration.md): successful authentication and private call-capture tooling.
- [Camera work](docs/camera.md): current evidence and remaining requirements.
- [Implementation provenance](docs/reference-integrations.md): comparison of the two public projects supplied by the owner.

The public integration by [daniel-mcdonough](https://github.com/daniel-mcdonough/cellgate-homeassistant) documents the mobile API's header/form request patterns. This project independently implements those protocol facts and adds bounded read recovery, no-replay command handling, concurrent session locking, strict gate/door filtering, and protocol tests. No source files were copied from that project.

## Validation

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

The 43 tests use synthetic data only. They check Home Assistant setup/unload, configuration, entities and unavailable states, plus wire formats, parsing, login rejection handling, concurrent session reuse, read-session recovery, permission failures, command uncertainty/no replay, SIP response parsing, SIP caching/refresh, and secret-safe object representation. The SIP and gate parsers were also exercised against private live responses; no account values are included in fixtures. Real gate actuation, incoming call routing and media remain unvalidated.

Authenticated SIP provisioning details: [verified request and response schema](docs/sip-provisioning.md).
