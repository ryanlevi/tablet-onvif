# Tablet ONVIF

Developer preview: actual Protect adoption is pending validation. Install, start and open the Web UI through Home Assistant ingress. Enable Camera Stream and Camera Motion in the iPad Companion App and keep it open in kiosk mode.

Select **Set up** next to your tablet. Keep its name or choose a new one, and enter its camera-stream login only if it uses a password. Select **Enable camera**. Bridge address, port and NVR credentials are configured automatically.

Each camera has **Live view** and **Connect to NVR** buttons. New cameras reuse the tablet stream username and password when provided; otherwise the bridge generates an NVR login. Existing NVR logins stay intact. The connection guide shows the address and login, with instructions for UniFi Protect. Networking is tucked under Advanced settings; a per-camera address override is available for unusual network layouts.
The camera card shows video and motion availability. Select **Live view** to open a continuous camera preview. The preview remains open during status refreshes and can diagnose the tablet feed even when RTSP encoding is unavailable. Its source credentials stay server-side. Preview success does not by itself prove that NVR video works. Configure Protect or another NVR with the displayed ONVIF endpoint and NVR credentials. RTSP reads use the same NVR credentials.

Motion is derived directly from the selected binary sensor; no MQTT or extra automation is needed. The app clears stale motion on HA disconnect/unavailable. Check the tablet's own clear delay settings to control event duration.

Networking uses the HA host LAN: administration 8099 (Supervisor ingress only), default RTSP 8556, UDP discovery 3702, and each camera's ONVIF port. Avoid port conflicts with existing services. Discovery stays within a subnet; manual adoption may be needed across VLANs.

Keep all ports local. Settings and credentials persist in app data. Back up app data to preserve camera UUIDs. Saving settings restarts the bridge. Stop/uninstall the app to roll back; this does not change your existing HA entities, automations, dashboards or Frigate hardware configuration.

## Debug logs

Open this app's **Configuration** tab, enable **Debug logging**, save, then restart it. Open Live view and reproduce the error once. The **Logs** tab will include connection stages, separate preview/encoder source requests, HTTP status, bytes delivered, sanitized encoder/server output and ONVIF requests. Turn Debug logging off and restart after diagnosis.

Secrets are redacted, but logs contain LAN addresses and entity IDs, so review before posting them publicly. If Video ready appears but Live view fails, include the `browser-preview` and `encoder-relay` lines around the failure.
