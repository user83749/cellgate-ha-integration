# Camera and two-way audio status

Version 0.3.0 contains experimental incoming SIP call handling and a standard Home Assistant WebRTC camera. Enable it in the integration's configuration options.

The camera is one persistent entity per account. It is unavailable to stream between calls and carries both video and audio during a call. Opening it while ringing answers. Closing the viewer does not end the call; the same camera can be reopened. CellGate's remote call-end signal releases the media and makes the camera unavailable again. No arbitrary accepted-call timer is applied.

Local real-media tests verify video/audio reception, microphone return and viewer reopen across one call. Live SIP registration and renewal are verified. A real CellGate incoming media call remains untested, so the feature is experimental.

See [implementation, supported codecs, dependencies and validation](call-media.md). No picture button, stored-picture substitute or HomeKit-specific code is included.
