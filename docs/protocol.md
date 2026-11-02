# Historical web-portal protocol observations

This is historical research. Version 0.2.0 of the active integration uses the mobile API; see [mobile route inventory](mobile-routes.md). The web transport is no longer imported.

Observed November 2, 2026, from CellGate's public portal assets:

- `https://user.zapopen.com/main.2c0e243ca4fd78bf.js`: API URLs, authentication service, interceptor, RSA transport codec, hardware constants.
- `https://user.zapopen.com/295.e2a7da42ff1f6b86.js`: dashboard service discovery and model-specific gate command construction.
- `https://user.zapopen.com/111.4dc548b4f2e8364d.js`: state-label mapping and dashboard behavior.
- `https://user.zapopen.com/146.05f5ec94af59e11d.js`: gate command menu and hold duration units (hours).

REA parsed the initial public main bundle without executing it. Parent evidence ID: `ev_70a4e06bc7b855123adb0dc33e0902b7f3eb692b6ae1ebf70fc502b1afb5f589`. Semantic graph coverage was partial. The focused protocol observations below come from direct static inspection of the relevant methods.

## Authentication and transport

`POST https://accountkit-user.zapopen.com/api/AccountKit/userlogin` wraps `{username,password}` as `{Data,Value}`. `Data` consists of base64 RSA PKCS#1 v1.5 encrypted chunks separated by `check`, including a trailing separator. The portal chunks strings at 86 UTF-16 units. The implementation JSON-escapes non-ASCII input to keep each chunk ASCII; the server decodes equivalent JSON. This equivalence still needs live validation.

The RSA public key and `Is_E` value are public constants shipped to every browser. They are not an account's private keys or session tokens. Responses are ordinary JSON. Login reads `AuthToken`, `user.SessionID`, `CsrfToken`, and `user.ID`.

Subsequent headers include `AuthToken: Bearer <token>`, `AuthKey`, `Is_E`, and `X-CSRF-Token`. Request `Value` encrypts the session ID. GET query strings are encrypted into `request` and the session into `value`.

Discovery: `GET /api/Dashboard/getModules`, with plaintext query `UserId=<ID>&AccountID=0` encrypted before transmission. The dashboard service expects an array of modules with `ModulePorts`, hardware information, and port state/configuration fields. Actual account response fixtures have not been captured; the integration's parser is based on these static property accesses.

Commands: `POST https://deviceaction.zapopen.com/api/<family>/<command>`, with a `W_e` header containing base64 of the module ID. Payloads include modem ID, serial, module and port IDs, module type, and port number. Momentary includes user ID and `IsFromAppTrigger`. Only the momentary command is implemented, per the owner's requested scope.

## State and outstanding checks

The portal maps `LastStateTypeID == 1` to `RelayLowStatus` and `== 2` to `RelayHighStatus`. These labels are configurable and should not be silently promoted to physical position.

The user's authenticated dashboard showed a gate on Relay 1, a reported Gate Closed state, and a camera. No control command or configuration save was performed. No account credentials or private account response fixtures are included here.

Before release: validate login/discovery against the actual account, verify account permission handling and command acknowledgments, confirm the selected hardware model, run Home Assistant integration tests, and coordinate a physically observed command test. Camera/video and two-way talk are requested but remain unimplemented pending call-session evidence and a verified media/audio transport. See [camera investigation](camera.md).

Home Assistant implementation references: [config flows](https://contributors.home-assistant.io/core/integrations/config-flow/) and [coordinated fetching](https://contributors.home-assistant.io/core/integrations/fetching-data/).
