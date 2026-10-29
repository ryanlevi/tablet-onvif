# 0.1.1

- Add a live MJPEG viewer to the ingress setup UI, available even before RTSP encoding succeeds.
- Preserve active live views during periodic status updates.
- Report tablet credential rejection, unavailable camera, redirected/wrong source URL and LAN connection failures.
- Keep source credentials server-side; use the existing ingress trust boundary and preview-only, short-lived HTTP-only cookies for standalone browser images.
- Add multipart video, authentication isolation and source error regression tests.

# 0.1.0

Initial developer preview: Companion App discovery, authenticated ONVIF media and PullPoint motion events, FFmpeg/MediaMTX video, ingress setup UI, Docker packaging and automated protocol tests. Actual UNVR adoption and complete image execution pending.
