# 0.4.4

- Fix codec lookup failures silently stripping video: unknown codec metadata now leaves H.264/VP8 available for Asterisk negotiation. A confirmed audio-only offer still remains audio-only.
- Expose viewer negotiation stage and safe provider/viewer audio/video packet counts on the HA camera. Keep the last call's counts after hangup so failures can be inspected afterward. Counts are sampled every five seconds while answered; unavailable counts are null, not fabricated zeros. These describe packets, not successful decoding.
- Return specific safe media failure codes to HA, log the failing stage and local SIP rejection status, and keep raw SDP and credentials out of camera errors.
- No gate behavior changes in this release. Real-device camera and talk still require a live incoming-call test.

# 0.4.3

- Gate: open/closed comes from CellGate's relay status (`lastState` 0 = closed, 1 = open). A configured label that names open or closed still takes precedence; other labels no longer turn the state into `unknown`.

# 0.4.2

- Gate: CellGate answers a queued open with `isSuccess: false` and result `Processing`; treat that as accepted instead of raising "CellGate rejected the mobile request".
- Gate: show `opening` for 15 seconds after Open and ignore repeat Open presses in that window, so a double tap (HomeKit or the dashboard) cannot send a second relay pulse.

# 0.4.1

- Advertise the public address (STUN, or `external_address`) in SIP and SDP, keep RTP flowing with keepalives and enable ICE toward CellGate. Previously the private LAN address was advertised, so intercom audio/video could not reach the backend.
- Accept the incoming call for any Request-URI user, from any provisioned CellGate address or via the registration's line parameter; stop identifying calls by SIP username.
- Recover a registration Asterisk gave up on, and refresh the public address when it changes.
- Connect audio/talk when the intercom offers no H.264/VP8 video instead of failing the call.
- Relay Asterisk notices/warnings (rejected calls, codec and NAT errors) into the add-on log.
- Gate: Open is never greyed out by the reported relay label and is not blocked by the cloud's online flag.
- Gate errors include CellGate's own rejection reason.
- Integration: **Download diagnostics** exports the mobile API's SIP, location and device structure (credentials redacted) and the intercom state.

# 0.4.0

Initial dedicated Asterisk companion: mobile-provisioned SIP registration, validated redirect routing, ARI-managed call lifetime, native HA SDP/ICE signaling, encoded video forwarding and microphone return. Experimental until a real CellGate call is validated.
