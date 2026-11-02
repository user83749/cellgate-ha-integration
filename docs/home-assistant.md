# Home Assistant compatibility

Install this custom integration under `/config/custom_components/cellgate/`. It uses HA's configuration UI, config-entry `runtime_data`, shared aiohttp session, coordinator, device/entity registries, in-place options updates and unload/shutdown callbacks.

The main gate entity is a `CoverEntity` with device class `gate`, reported open/closed state and only the supported open action. The old trigger button is retired without deleting its registry metadata. A raw diagnostic sensor is disabled by default for new installations.

Opt-in call support adds a persistent `Camera` with native asynchronous WebRTC hooks and a standard doorbell `EventEntity`. Entity states and events contain no SIP credentials or SDP. Runtime resources are released on call end and config-entry unload. Viewer closure ends only the WebRTC viewer, not the SIP call.

Tests exercise the actual Core 2026.10.4 loader and entity platforms with synthetic mobile data. The packaged companion test exchanges real SIP/RTP/WebRTC media through Asterisk using a simulated intercom and the actual HA signaling manager. Historical receiver tests remain archived regression coverage. No test actuates a physical gate. The official hassfest validator passes with zero invalid integrations, and the installed custom component no longer requires third-party media packages.

Core 2026.11.0b3's real native camera/two-way-audio capability interface passes a separate smoke check. Full beta lifecycle tests are not claimed: the published custom-component test helper targets 2026.10.4 and imports symbols removed in the beta. Custom integrations' boolean setup/unload return values remain accepted by the beta.

For the standard microphone interface, Core 2026.11 requires its Labs two-way-audio preview, a WebRTC stream and HTTPS. Declaring the capability alone does not provide an audio transport; this implementation also supplies a microphone return path tested against a simulated gate.

See [call/media details and supported profile](call-media.md) and [installation](../README.md).

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
```

The GitHub workflow runs tests and hassfest on future pushes. Passing validation does not constitute Home Assistant endorsement. Installation on the owner's HA instance and real incoming CellGate media remain untested.
