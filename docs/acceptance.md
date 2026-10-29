# Acceptance and deployment gate

Automated tests are necessary but do not establish NVR interoperability. Record the client/HA/Companion versions and results for each release. Never declare Protect support based only on generated XML or a successful HTTP login.

## Automated checks

- Correct WS-Security password digest accepted; wrong user/password, old/future timestamps and nonce replay rejected.
- HTTP Digest verifies the method, request target, signed nonce and nonce count; malformed credentials rejected.
- Actual HTTP SOAP requests support WS-Security without requiring HTTP Basic first.
- XML parsers reject DTD/entity expansion; responses are parseable and contain no source passwords.
- Motion initialization, active/clear edges, duplicate suppression, HA unavailable handling and per-subscriber isolation.
- Subscription expiration, renewal, maximum clients and queue bounds.
- HA entity discovery follows registry device ownership rather than display names.
- Ingress rejects forged headers from unauthorized peers; standalone API requires its token; config redacts secrets.
- Media server configuration must validate against the pinned MediaMTX release.

## Real deployment checklist (pending)

1. Build amd64 and aarch64 images. Install through HA OS local Apps store. Check ingress and Supervisor authentication.
2. Enable Camera Stream and Camera Motion in Companion App. Set source credentials locally. Verify one current snapshot and playable main/sub RTSP streams in VLC or FFmpeg.
3. Reject wrong ONVIF and RTSP credentials. Adopt in UNVR with real NVR credentials and verify GetStreamUri resolves to a reachable address.
4. Verify Protect live view, recording, thumbnails and refresh after encoder restart. Confirm it is not adopting an old go2rtc service at a different port.
5. Subscribe and trigger a physical movement in view of the iPad. Confirm HA `on`, ONVIF IsMotion true, Protect timeline event, HA `off`, ONVIF IsMotion false. Repeated `on` updates must not flood events.
6. Disconnect HA while motion is true. Confirm diagnostics turn unavailable and the motion event clears. Reconnect and synchronize current state.
7. Background/lock iPad, disable Camera Stream, deny camera permission and interrupt Wi-Fi. Mark stream unavailable, serve no stale snapshots and recover when the source returns.
8. Restart app, HA and tablet independently. Preserve identity and NVR adoption. Exercise subscription renewal for at least an hour.
9. Check CPU, memory and iPad temperature for sustained use. Verify 24-hour video/event stability with multiple readers.
10. Test multiple tablets; ensure Protect distinguishes actual network identity. If it deduplicates by physical MAC, document/configure real virtual interfaces before claiming multi-camera compatibility.

Rollback: stop/uninstall Tablet ONVIF, preserve/export its data if needed, and remove the virtual camera from Protect with Archive if retaining recordings. Existing Frigate configuration, hardware mappings and Hailo/GPU setup are not involved.
