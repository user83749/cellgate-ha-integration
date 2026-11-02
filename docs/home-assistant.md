# Home Assistant compatibility

This project is a custom integration, installed as `/config/custom_components/cellgate/` and configured under Settings → Devices & services. It is not a Supervisor app or a HomeKit accessory server. Home Assistant itself owns any subsequent bridge configuration.

## Runtime

- The manifest declares a `config_flow`, `cloud_polling`, a version, code owner, documentation and issue tracker.
- The UI flow authenticates through the mobile API, discovers gates, prevents duplicate account entries and supports reauthentication.
- Config-entry `runtime_data` owns the coordinator and mobile client. The coordinator uses Home Assistant's shared aiohttp session, an explicit config entry, asynchronous I/O and a 60-second polling interval.
- Platforms create standard `ButtonEntity` and `SensorEntity` instances with stable account/device/port identifiers and device-registry entries.
- Commands send once, with no automatic replay after uncertain acknowledgement. Connectivity failures mark entities unavailable; rejected authentication starts reauthentication.
- Config-entry unloading removes listeners and polling through the coordinator lifecycle and clears cached API/SIP authentication. The shared HTTP session remains owned by Home Assistant.

## Validation

The real Home Assistant 2026.10.4 runtime was installed in an isolated Python 3.14 environment. Tests use synthetic responses, including the trigger action; they never send gate commands. They cover setup, entity creation, button dispatch, reported state, loss of availability, unload cleanup, configuration flow, duplicate prevention and reauthentication initiation.

Run all tests with:

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

The official hassfest validator from Home Assistant 2026.10.4 completed locally with zero invalid integrations, including manifest, translations, dependencies and configuration-flow checks. The GitHub Actions workflow repeats hassfest and the test suite on future pushes. Passing local runtime checks is not Home Assistant endorsement or inclusion in core. Installation on the owner's instance has not been tested.

## Camera and talk requirements

No camera entity or two-way-audio capability is advertised yet. A real incoming SIP call and its negotiated SDP/media must be handled first. The registration diagnostic in `tools/sip_probe.py` is separate from the integration and is not imported by its runtime. It does not implement a production SIP stack or a media bridge.

A future camera implementation must use Home Assistant's camera and WebRTC APIs and clean up the call/media resources on call end and entry unload. Home Assistant Core 2026.11 introduces a preview two-way-audio capability; it requires actual audio return over the streaming transport, WebRTC delivery and the Labs setting. Merely marking a capability true is insufficient.

References:

- https://contributors.home-assistant.io/core/integrations/manifest/
- https://contributors.home-assistant.io/core/entities/camera/
- https://contributors.home-assistant.io/blog/2026/10/24/camera-two-way-audio/
- https://github.com/home-assistant/actions/tree/master/hassfest
