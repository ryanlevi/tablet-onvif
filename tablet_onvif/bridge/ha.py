"""One authenticated HA WebSocket: registry discovery + current states + motion edges."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .diagnostics import endpoint

LOG = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self, url, token, changed):
        self.url, self.token, self.changed = url, token, changed
        self.states, self.entities, self.devices = {}, {}, {}
        self.connected = False
        self.error = "Not connected"

    @property
    def websocket_url(self):
        u = urlsplit(self.url.rstrip("/"))
        path = u.path + ("/websocket" if u.hostname == "supervisor" and u.path == "/core" else "/api/websocket")
        return urlunsplit(("wss" if u.scheme == "https" else "ws", u.netloc, path, "", ""))

    async def notify(self):
        await self.changed()

    async def run(self):
        delay = 1
        while True:
            phase = "connect"
            LOG.debug("Home Assistant WebSocket connecting: %s", endpoint(self.websocket_url))
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)) as session:
                    async with session.ws_connect(
                        self.websocket_url, heartbeat=25, max_msg_size=16 * 1024 * 1024
                    ) as ws:
                        phase = "authentication"
                        hello = await ws.receive_json(timeout=10)
                        if hello.get("type") != "auth_required":
                            raise ValueError("Unexpected Home Assistant handshake")
                        await ws.send_json({"type": "auth", "access_token": self.token})
                        if (await ws.receive_json(timeout=10)).get("type") != "auth_ok":
                            raise ValueError("Home Assistant authentication rejected")
                        LOG.debug("Home Assistant authentication accepted; loading registries and states")
                        phase = "subscriptions"
                        # Subscribe first, then hydrate; don't miss changes between discovery and subscription.
                        requests = {
                            1: {"type": "subscribe_events", "event_type": "state_changed"},
                            2: {"type": "get_states"},
                            3: {"type": "config/entity_registry/list"},
                            4: {"type": "config/device_registry/list"},
                            5: {"type": "subscribe_events", "event_type": "entity_registry_updated"},
                            6: {"type": "subscribe_events", "event_type": "device_registry_updated"},
                        }
                        for key, request in requests.items():
                            await ws.send_json({"id": key, **request})
                        self.connected = True
                        self.error = ""
                        delay = 1
                        async for message in ws:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            data = message.json()
                            if data.get("type") == "result":
                                key = data.get("id")
                                if not data.get("success"):
                                    LOG.debug("Home Assistant request rejected: operation_id=%s", key)
                                    if key in (1, 2):
                                        raise ValueError("HA state subscription failed")
                                    self.error = (
                                        "Registry discovery unavailable; manual entity selection is still supported"
                                    )
                                    continue
                                result = data.get("result", [])
                                if key in (2, 3, 4):
                                    LOG.debug(
                                        "Home Assistant data loaded: operation_id=%s entries=%d", key, len(result)
                                    )
                                if key == 2:
                                    self.states = {s["entity_id"]: s for s in result}
                                elif key == 3:
                                    self.entities = {s["entity_id"]: s for s in result}
                                elif key == 4:
                                    self.devices = {s["id"]: s for s in result}
                            elif data.get("type") == "event":
                                if data.get("id") in (5, 6):
                                    key = 3 if data["id"] == 5 else 4
                                    await ws.send_json({"id": key, **requests[key]})
                                else:
                                    event = data.get("event", {}).get("data", {})
                                    entity_id = event.get("entity_id")
                                    if entity_id:
                                        if event.get("new_state"):
                                            self.states[entity_id] = event["new_state"]
                                        else:
                                            self.states.pop(entity_id, None)
                            await self.notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never log exception text: HTTP errors may embed tokens/URLs.
                LOG.warning("Home Assistant connection interrupted (%s); retrying", type(exc).__name__)
                LOG.debug(
                    "Home Assistant failure: phase=%s exception=%s retry_seconds=%d", phase, type(exc).__name__, delay
                )
                self.error = "Home Assistant connection unavailable; reconnecting"
            finally:
                self.connected = False
                self.states = {}
                await self.notify()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def state(self, entity_id):
        return self.states.get(entity_id, {}).get("state", "unavailable")

    def stream_url(self, entity_id):
        attr = self.states.get(entity_id, {}).get("attributes", {})
        for key in ("Stream URL", "stream_url", "streamUrl"):
            if attr.get(key):
                return attr[key]
        return ""

    def tablets(self):
        result = []
        for entity_id, entry in self.entities.items():
            if entry.get("platform") != "mobile_app" or not entity_id.startswith("sensor."):
                continue
            if not (
                entry.get("original_name") == "Camera Stream" or entry.get("unique_id", "").endswith("_cameraStream")
            ):
                continue
            device_id = entry.get("device_id")
            siblings = (
                [(eid, e) for eid, e in self.entities.items() if e.get("device_id") == device_id] if device_id else []
            )
            motion = next(
                (
                    eid
                    for eid, e in siblings
                    if eid.startswith("binary_sensor.")
                    and (e.get("original_name") == "Camera Motion" or eid.endswith("_camera_motion"))
                ),
                "",
            )
            device = self.devices.get(device_id, {})
            result.append(
                {
                    "device_id": device_id,
                    "name": device.get("name_by_user") or device.get("name") or entity_id,
                    "stream_entity": entity_id,
                    "motion_entity": motion,
                    "stream_state": self.state(entity_id),
                    "motion_state": self.state(motion),
                    "stream_url_available": bool(self.stream_url(entity_id)),
                }
            )
        return result
