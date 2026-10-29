# Security

Developer preview: deploy only on a trusted local network while interoperability testing is in progress. Never port-forward ONVIF, RTSP or the setup interface to the internet. HTTP Basic/RTSP credentials and video do not provide transport encryption on an unencrypted LAN; use trusted VLANs/firewall rules or a secure tunnel.

The HA Supervisor token is privileged. Use a dedicated HA account/access token in standalone mode and grant only the rights needed for states and registry discovery where possible. A source URL is an administrator-controlled LAN resource; the source selector is not safe for untrusted users. Numeric loopback/link-local/multicast sources and embedded URL credentials are rejected, but hostname resolution is not an SSRF isolation boundary. Network egress controls remain useful on shared networks.

Do not submit passwords, source URLs with credentials, access tokens, `/data` contents, raw HA state dumps or process environments in issues. The setup UI and coarse diagnostics are intended for sharing after review. Rotate any credential accidentally disclosed. Secrets are stored in restricted files, not encrypted at rest; protect host backups.

The ONVIF endpoint supports WS-Security UsernameToken PasswordDigest with timestamp and replay checks, HTTP Digest, and Basic. Protect authentication behavior must be verified on an actual UNVR. Adding a password to the admin UI is not a substitute for ONVIF authentication. New cameras reuse authenticated iPad stream credentials for NVR access. Without source authentication, the bridge generates a strong per-camera NVR login. Existing NVR logins are preserved.

To report a security defect before a public remote repository exists, contact the repository owner directly. After publication, enable GitHub private vulnerability reporting. Do not publish exploit details or secrets in a public issue.

Standalone live previews use a short-lived HTTP-only, SameSite=Strict cookie limited to `/api/live/`. It authorizes only camera preview requests, not configuration access. HA ingress previews continue to require the Supervisor peer address. Camera source credentials and the admin bearer token are not placed in image URLs.

The Connect to NVR dialog intentionally reveals a camera’s NVR credentials only after an authenticated user opens it. Normal configuration, status, and setup responses remain redacted. Closing the dialog clears the password input. Shared credentials inherit the tablet password strength. Changing a linked source login also changes its NVR login; removing source authentication generates a strong NVR login. The reserved MediaMTX username `any` is rejected. Repeated usernames are permitted, with RTSP access scoped to each camera path.

Debug logging is off by default. A redacting formatter applies after exception formatting; child-process records are redacted before they are logged. Runtime tokens, camera passwords, encoded Basic credentials, URL credentials/query/path components and authorization headers are removed. No raw settings, option files, HTTP headers or SOAP bodies are logged intentionally. Diagnostic output still includes IP addresses, camera/entity identifiers, status codes and operation names. Very long child records are discarded as a whole to prevent fragments crossing a truncation boundary.
