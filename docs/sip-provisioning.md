# Verified mobile SIP provisioning

Authorized live requests on November 2, 2026 established that SIP account information is available **before an incoming call**. One mobile login succeeded, followed by SIP-information, location-list, and device-list reads. No SIP registration, call acceptance, media connection, or gate command was performed.

## Request

```http
POST https://cellgate.zapopen.com/api/CellCam/GetSipInformationForUser
Authorization: Bearer <token-from-mobile-login>
AppVersion: 17
Accept: application/json
```

No form or JSON body was required for this account. The response was HTTP 200 with `isSuccess: true` and an object at `data.sipInfo.account`.

The successful mobile login also included the same configuration at:

```text
sipUser.IsSipAppUser == true
sipUser.ResponseDetails.account
```

The three native account fields (`username`, `password`, `host`) agreed between these two responses. The integration can therefore obtain the account during ordinary login and avoid an extra request unless a refresh is explicitly needed.

## Response schema

The account object contains these string fields; no private values are reproduced here:

| Field | Observed role |
| --- | --- |
| `username` | Read by native SIP account setup |
| `password` | Read by native SIP authentication setup |
| `host` | Read by native SIP server/address setup |
| `sipisRegServer` | Present but empty in this response; optional registrar semantics remain unverified |
| `codecOrder`, `codecOrder3G` | Comma-separated numeric audio codec configuration |
| `videoCodecOrder`, `videoCodecOrder3G` | Comma-separated numeric video codec configuration |
| `dtmfOrder` | DTMF configuration; `rfc2833` observed |
| `extProvInterval` | Numeric string; `3600` observed; timer semantics not runtime-tested |
| `cloud_username`, `cloud_password` | Also present; not used or retained by the integration's SIP parser |
| `extProvUrl`, `title` | Also present; not used by the SIP parser |

Do not infer the actual negotiated audio/video codecs solely from numeric IDs: some RTP payload types are dynamically negotiated.

## Implementation and credential handling

`SipAccount` is a private, immutable data object with representation disabled. It is not inserted into coordinator state, device attributes, entity attributes, or logs. The client caches the parsed account in memory. `get_sip_account(refresh=True)` re-fetches the configuration through the verified endpoint; it does not call the separate, still-untraced `RefreshSIPToken/` route.

The account password used for the diagnostic was delivered through a local restricted pipe, not shell arguments, an environment variable, or a password file. Raw diagnostic replies are kept only in a mode-0700 temporary directory with mode-0600 files, outside the project. No account password, Bearer token, SIP credential, or private response body is included in the repository or test fixtures.

## Discovery results

The mobile service returned one device of hardware type `WTM-EVO`, with a Gate port and a Camera port. The existing gate parser correctly excluded the Camera port from triggers. The response also supplied `descriptionState0` and `descriptionState1`, so the sensor now reports the server-provided label for the current raw `lastState` value. This remains a reported state, not independent verification of physical position.

## Later registration result

A subsequent authorized diagnostic successfully registered with SIP/UDP and removed its own contact. See [registration results](sip-registration.md). This was a separate development probe; the Home Assistant runtime still has no SIP transport or media backend.

## Remaining work

Credentials are now retrieved and parsed. Production SIP registration lifecycle, call routing, simultaneous-client behavior, push/wake handling, SDP negotiation, RTP/SRTP media transport, and two-way audio integration are not yet implemented or tested in Home Assistant. Mobile login concurrency has not been established by these read-only checks.
