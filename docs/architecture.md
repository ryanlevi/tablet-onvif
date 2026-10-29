# Architecture

The product ships as one container with three processes: a Python asyncio service, FFmpeg encoders and MediaMTX. There is no dependency on go2rtc or Frigate. The ONVIF implementation is original and intentionally limited to the services we can validate.

## Trust boundaries

Administration uses a separate HTTP listener (8099). Home Assistant ingress requests must originate from the Supervisor ingress address, not just carry a forged header. Standalone administration requires a persistent bearer token and binds to localhost by default. ONVIF never passes through the administrator authentication middleware.

Each camera has its own ONVIF listener, identity, credential verifier and subscription store. WS-Security authenticates SOAP requests directly. HTTP Digest is supported for clients and snapshots. HTTP Basic is available for clients which use it; it does not block valid WS-Security tokens. GetSystemDateAndTime is public to permit clock bootstrap. Authenticated RTSP reads use the same NVR credential pair in MediaMTX. UsernameToken timestamps are bounded to five minutes and nonces cannot be replayed.

## Video

A trusted administrator selects a tablet's entity and credentials. The HA WebSocket client obtains its current Stream URL attribute and stream availability. FFmpeg reads a loopback relay which supplies tablet HTTP Basic credentials without putting those credentials in FFmpeg command-line arguments. Redirects are refused. Two H.264 encoders provide fixed-size main/sub streams with letterboxing; an image output supplies current snapshots. The output uses software encoding in this preview. Hardware acceleration can be added after correctness and platform measurements.

FFmpeg publishes over loopback to MediaMTX. Loopback publication is allowed, external publication is not. Remote readers need per-camera NVR credentials restricted to that camera's two paths. MediaMTX control, HLS, WebRTC, RTMP, SRT and metrics listeners are disabled. A progress watchdog uses snapshot updates to restart a stalled encoder. A supervisor retries failed video workers and restarts MediaMTX after unexpected termination; availability remains false until fresh frames return.

The iPad password, HA token and NVR password are separate secrets. Configuration responses redact passwords; user-selected source URLs reject embedded credentials.

## Home Assistant

The app authenticates to HA using SUPERVISOR_TOKEN, or HA_TOKEN in standalone Docker. It subscribes to state changes before loading states, and loads entity/device registries to associate stream and motion sensors by device ownership. Registry change events refresh discovery. On disconnect it clears its state cache, clears stale active motion and reconnects with bounded exponential backoff. No retained MQTT messages or automations are involved.

## ONVIF scope

Implemented operations are enumerated in `bridge/onvif.py`: Device information/time/capabilities/services/scopes/interfaces, Media profiles/sources/encoder descriptions/stream and snapshot URIs, and Events properties/subscription/pull/renew/unsubscribe/synchronization. Discovery answers Probe/Resolve and sends Hello/Bye. Discovery replies are rate-limited. PullPoint queues, subscription counts, SOAP request size, authentication replay cache and wait durations are bounded.

This is an interoperability implementation, not a claim to complete Profile S/T conformance. PTZ, audio, stream configuration mutation, WS-BaseNotification push subscriptions, AI labels, replay/recording services and camera settings writes are not advertised as supported. ONVIF compatibility must be checked with real clients before a stable release.

## Data and configuration

`/data/settings.json` contains settings and passwords, mode 0600. Installation UUID and standalone admin token live alongside it. Saves validate before stopping the runtime; a failed apply attempts to restore the prior runtime. Changes restart all media streams. The UI never receives saved passwords. Empty secret fields retain prior values; to remove source credentials, clear the source username or remove/recreate the camera.
