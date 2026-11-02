# Reference integration comparison

Reviewed November 2, 2026, from repositories supplied by the owner. Neither project was installed, started, or supplied with account credentials.

## daniel-mcdonough/cellgate-homeassistant

Repository: https://github.com/daniel-mcdonough/cellgate-homeassistant

Reviewed commit: `2f72a80e84a350bd9be102719190b84c5e3e3083`.

- Uses the **mobile API**, `https://cellgate.zapopen.com/api/CellCam`.
- Documents header-based POST login (`AuthUsername`, `AuthPassword`, empty push tokens, compatibility AppVersion `17`, AppType `android`). Login response includes `isSuccess` and `token`.
- Documents header-based `GetMapLocationList` and form-based `DevicesList` and `GateCommand`.
- Documents the misspelled response key `ProertyLocaitonId`.
- Provides momentary gate buttons. Its README says it reversed the Android app, found Linphone, and does not plan SIP support.
- No live camera or two-way audio implementation was found.

This is a source-backed contract reference, not a runtime observation of the owner's account. Its compatibility AppVersion is distinct from the public Android version name `4.0.61`.

The new client independently implements these protocol facts. Source files were not copied from the reference repository. The route inventory marks which contracts come from this source and which remain only static literals in the Android artifact.

## chelin1974/CellGateHome

Repository: https://github.com/chelin1974/CellGateHome

Reviewed commit: `3928d5feab530fe37098d317d217c5d3da7dd965`.

- Uses the **web portal API**, including AccountKit login, RSA transport wrapping, and model-specific DeviceAction requests.
- Is a standalone Java bridge rather than a Home Assistant custom integration.
- Its camera output is the most recent stored still image, optionally repeated by FFmpeg; this is not an intercom live video session.
- Infers a doorbell event from activity-log image entries. It does not establish a real incoming-call event or SIP session.
- No Linphone/SIP call receiver or two-way talk implementation was found.

Its stored-picture behavior is outside the owner's requested scope and was not incorporated.

## Migration decision

Use the mobile API as the Home Assistant HTTP backend. Do not assume that a web account session carries mobile SIP credentials. Do not infer session concurrency from route names. Implement actual camera/talk only after mobile SIP provisioning, registration, and media behavior are understood.

The legacy web transport is preserved only as ignored local research. It is not imported or used by the active custom component.
