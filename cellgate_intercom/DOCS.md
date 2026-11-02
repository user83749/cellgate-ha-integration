# CellGate Intercom setup

This companion service runs Asterisk for the CellGate Home Assistant custom integration. It is a media backend; the integration supplies the standard HA gate, camera and call-event entities. The TECH7Fox Asterisk HA integration and a custom SIP dashboard card are not required.

## Configuration

| Option | Default | Purpose |
| --- | --- | --- |
| `api_token` | empty; must be set | Independent 32–128 character random token, copied into the HA integration options |
| `api_port` | 28190 | Authenticated HA-to-backend HTTP signaling |
| `sip_port` | 15060 | Dedicated Asterisk provider SIP/UDP socket |
| `asterisk_http_port` | 18088 | ARI and SIP-over-WebSocket, bound to loopback only |
| `rtp_start` / `rtp_end` | 28000 / 28200 | Asterisk UDP media range; start must be even |
| `stun_server` | stun.l.google.com:19302 | STUN discovery for WebRTC ICE; empty disables it |
| `external_address` | empty | Public IP or DNS hostname for SIP/media address advertisement, when required by NAT |

Use an IP/DNS hostname without `http://` or a port in `external_address`; your existing public DNS name can be used there. It is distinct from the HA integration's backend URL, which is normally `http://HOME_ASSISTANT_LAN_IP:28190`. The browser uses the ICE addresses in Asterisk's answer, so both local and remote clients need a reachable media route.

Host networking is required. The control API is intended for the trusted HA network and requires the independent token. A public discovery endpoint reports only the product/API version and readiness; the integration checks this before sending a token, so a mistaken HA dashboard URL receives no backend credentials. Do not expose the backend HTTP API or Asterisk's local ARI port through the HA remote-access URL. Existing remote HTTPS access to HA handles the UI; it does not automatically relay WebRTC media across every NAT/firewall. TURN relay configuration is not included. The actual CellGate call's NAT behavior still needs a live test.

The service accepts one configured CellGate account. The custom integration retrieves SIP provisioning from the mobile API and configures Asterisk automatically. Do not paste the CellGate account login password into the add-on. Its `api_token` is a separate secret. The service has no HA/Supervisor API permissions.

Registration redirect discovery uses a credential-free, contact-specific `REGISTER` with `Expires: 0`; it cannot install an incoming-call contact. Asterisk then performs authenticated registration to the validated next hop, retaining the original identity/Request-URI. Registration expiry is requested at 60 seconds; OPTIONS maintains the UDP path every 25 seconds. If registration is lost and the next hop changes, discovery is retried at most once a minute outside an active call. Authentication failures at an unchanged next hop do not trigger repeated forced reloads.

Normal shutdown explicitly deregisters while authentication remains available, then removes the provider configuration. A crashed service's contact can persist until the provider's registration expiry. Runtime SIP config is private (`0600`, directory `0700`), cleared at startup and normal shutdown; it may remain in the add-on data volume after an abrupt crash until restart. ARI/local SIP passwords are generated for each process.

The backend preserves an answered provider channel and bridge while HA viewers detach. It answers only when a valid viewer joins. The caller's BYE/CANCEL, disabling the integration, or stopping the backend releases resources. One HA viewing/talk leg is supported at a time.

## Home Assistant Container

Build from this repository on the Linux host:

```sh
docker build -t cellgate-intercom:0.4.0 cellgate_intercom
mkdir -p cellgate-intercom-data
chmod 700 cellgate-intercom-data
```

Create `cellgate-intercom-data/options.json` with a generated secret:

```json
{
  "api_token": "REPLACE_WITH_AN_INDEPENDENT_RANDOM_TOKEN_AT_LEAST_32_CHARACTERS",
  "api_port": 28190,
  "sip_port": 15060,
  "asterisk_http_port": 18088,
  "rtp_start": 28000,
  "rtp_end": 28200,
  "stun_server": "stun.l.google.com:19302",
  "external_address": ""
}
```

Then, with an absolute path for the data directory:

```sh
docker run -d --name cellgate-intercom --restart unless-stopped \
  --network host \
  -v /absolute/path/cellgate-intercom-data:/data \
  cellgate-intercom:0.4.0
```

In HA, configure the integration with the Linux host's reachable address and the API port/token. This image uses the referenced TECH7Fox Asterisk 6.4.0 image (Asterisk 22.11.0) with a dedicated Python controller/configuration; its normal Supervisor startup is bypassed. It does not alter an existing PBX installation. amd64 is exercised in CI; the base image also supplies aarch64, but execution on ARM64 hardware has not been validated here.

## Diagnostics

The add-on log reports startup/configuration failures without credentials. The HA camera attributes expose `call_state` and a sanitized `last_error`, never SDP or SIP credentials. `configuration_required` means the new backend URL/token has not been entered. Authentication rejection stops retries until options are updated. Keep the backend token consistent between the add-on and integration.

If the camera cannot find a common video codec/profile, the session fails instead of silently re-encoding. A native HA offer may trickle ICE candidates; the adapter collects them (up to five seconds) before submitting a complete SIP offer. This can affect connection setup time; no lower-latency claim has been measured.
