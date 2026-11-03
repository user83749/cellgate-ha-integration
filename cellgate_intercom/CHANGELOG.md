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
