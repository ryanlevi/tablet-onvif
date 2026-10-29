# Tablet ONVIF

Developer preview: actual Protect adoption is pending validation. Install, start and open the Web UI through Home Assistant ingress. Enable Camera Stream and Camera Motion in the iPad Companion App and keep it open in kiosk mode.

Select your tablet, set the HA host LAN IPv4, choose a free ONVIF port, and enter a unique NVR username/password (at least 12 characters). Source username/password are the separate credentials configured on the tablet. The stream URL normally comes from the Camera Stream sensor's Stream URL attribute. Save to start video and event services.

The status area shows video availability, motion state, subscriptions and coarse source errors. Select **Show live stream** to open a continuous camera preview. The preview remains open during status refreshes and can diagnose the tablet feed even when RTSP encoding is unavailable. Its source credentials stay server-side. Preview success does not by itself prove that NVR video works. Configure Protect or another NVR with the displayed ONVIF endpoint and NVR credentials. RTSP reads use the same NVR credentials.

Motion is derived directly from the selected binary sensor; no MQTT or extra automation is needed. The app clears stale motion on HA disconnect/unavailable. Check the tablet's own clear delay settings to control event duration.

Networking uses the HA host LAN: administration 8099 (Supervisor ingress only), default RTSP 8556, UDP discovery 3702, and each camera's ONVIF port. Avoid port conflicts with existing services. Discovery stays within a subnet; manual adoption may be needed across VLANs.

Keep all ports local. Settings and credentials persist in app data. Back up app data to preserve camera UUIDs. Saving settings restarts the bridge. Stop/uninstall the app to roll back; this does not change your existing HA entities, automations, dashboards or Frigate hardware configuration.
