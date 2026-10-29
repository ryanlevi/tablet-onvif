# Local validation record — developer preview

Validated on the development Mac during initial creation:

- 57 Python tests pass: authentication (WS-Security/Basic/Digest/replay/time window), SOAP HTTP requests, motion delivery/clear/subscription behavior, XML parser hardening, configuration secrecy/permissions, ingress access control, actual mock HA WebSocket state changes/disconnect, source credential relay and RTSP access configuration.
- Ruff source checks and JavaScript syntax check pass.
- App metadata, repository metadata, Compose and CI YAML parse.
- Pinned MediaMTX 1.21.2 official native Darwin ARM64 binary accepted the generated configuration (`--validate-conf`) and started/stopped its RTSP listener. A DESCRIBE request reached the listener (404 expected without a publisher). This does not prove remote authentication or encoded video delivery.

Not run locally: Docker image build/execution (Docker is unavailable on the development Mac), FFmpeg encoding (no local FFmpeg), Supervisor installation, real iPad camera stream and physical motion, ONVIF discovery on the user's LAN, real Protect adoption/recording/timeline events. CI includes amd64/arm64 image build jobs once hosted on GitHub.

Keep these distinctions visible in issue reports and release notes. A green unit test suite is not a completed Protect acceptance test.

Live-preview correction (0.1.1): an actual browser rendered successive synthetic JPEG frames through the authenticated live relay and retained the viewer across status polls. Multipart regression tests check two complete frames, private source authentication, preview cookie scope/expiration, and safe errors for rejected credentials, redirects and non-video URLs. Real iPad and HA ingress deployment validation remain separate.

MVP setup (0.2.0): browser-checked selecting a discovered tablet, enabling it without entering addresses/ports/NVR credentials, opening live video, and viewing the generated Protect connection guide. Regression tests cover generated defaults, preserving existing tuning and credentials, optional source-password handling, disabled-device guidance and authenticated credential retrieval. These use synthetic camera fixtures.

Debug logging (0.2.1): tested Supervisor option precedence/default-off behavior, raw/URL-encoded/base64 credential redaction, authorization header and URL token removal, redaction of exception details, child-pipe draining and dropping entire oversized records. Real browser-preview failure diagnosis still requires logs from the installed app.
