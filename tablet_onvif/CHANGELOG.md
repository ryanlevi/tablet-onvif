# 0.2.1

- Add an opt-in Debug logging switch in the Home Assistant app Configuration tab (off by default).
- Log HA connection stages, registry loading, motion transitions, preview/source HTTP responses and stream delivery, encoder startup/exit, and ONVIF authentication/subscriptions.
- Capture redacted FFmpeg and MediaMTX process diagnostics instead of discarding their output.
- Distinguish browser-preview, source-check and encoder-relay connections to investigate live-view failures when encoded video is ready.
- Redact passwords, API tokens, authorization headers and URL credentials/paths; bound child log records and output volume.

# 0.2.0

- Simplify setup to choosing a tablet, naming it and optionally entering its stream credentials.
- Detect the bridge LAN address, select an available camera port and generate NVR credentials automatically.
- Add a Connect to Protect guide with explicit credential reveal/copy and optional manual adoption details.
- Show existing cameras as compact live-view cards; move networking into collapsed Advanced settings.
- Preserve existing camera identity, passwords, stream tuning and ports when editing a camera.
- Automatically refresh device discovery and provide actionable messages when tablet sensors are disabled.

# 0.1.1

- Add a live MJPEG viewer to the ingress setup UI, available even before RTSP encoding succeeds.
- Preserve active live views during periodic status updates.
- Report tablet credential rejection, unavailable camera, redirected/wrong source URL and LAN connection failures.
- Keep source credentials server-side; use the existing ingress trust boundary and preview-only, short-lived HTTP-only cookies for standalone browser images.
- Add multipart video, authentication isolation and source error regression tests.

# 0.1.0

Initial developer preview: Companion App discovery, authenticated ONVIF media and PullPoint motion events, FFmpeg/MediaMTX video, ingress setup UI, Docker packaging and automated protocol tests. Actual UNVR adoption and complete image execution pending.
