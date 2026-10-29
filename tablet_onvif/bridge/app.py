from __future__ import annotations

import asyncio
import hmac
import hashlib
import time
import logging
import os
import signal
from pathlib import Path

from aiohttp import web

from .config import Settings, Store
from .discovery import Discovery
from .diagnostics import REDACTOR, configure_logging, debug_option, register_camera
from .ha import HomeAssistant
from .media import MediaServer, Video
from .onvif import Onvif
from .setup import prepare_camera

LOG = logging.getLogger(__name__)


class Bridge:
    def __init__(self, store):
        self.store = store
        self.settings = store.load()
        REDACTOR.add(store.admin_token)
        for camera in self.settings.cameras:
            register_camera(camera)
        self.ha = None
        self.ha_task = None
        self.media = None
        self.cameras = []
        self.videos = []
        self.discovery = None
        self.errors = []
        self.tasks = []
        self.lock = asyncio.Lock()
        self.setup_lock = asyncio.Lock()
        self.supervisor = bool(os.environ.get("SUPERVISOR_TOKEN"))

    async def changed(self):
        if not self.ha:
            return
        for camera in self.cameras:
            state = self.ha.state(camera.camera.motion_entity)
            previous = (camera.events.motion, camera.events.available)
            camera.events.update(state)
            if previous != (camera.events.motion, camera.events.available):
                LOG.debug(
                    "Camera %s motion=%s available=%s", camera.camera.id, camera.events.motion, camera.events.available
                )

    async def start(self):
        token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN", "")
        self.ha = HomeAssistant(self.settings.ha_url, token, self.changed)
        self.ha_task = asyncio.create_task(self.ha.run(), name="ha-websocket")
        if not token:
            self.errors.append(
                "Home Assistant token missing. Set HA_TOKEN for Docker or enable homeassistant_api in the HA app."
            )
        enabled = [c for c in self.settings.cameras if c.enabled]
        LOG.debug("Starting bridge: cameras=%d rtsp_port=%d", len(enabled), self.settings.rtsp_port)
        if not enabled:
            return
        self.media = MediaServer(self.store.directory, self.settings.rtsp_port)
        await self.media.start(enabled)
        for c in enabled:
            video = Video(c, self.ha, self.settings.rtsp_port, self.store.directory)
            onvif = Onvif(c, self.store.camera_uuid(c), video)
            self.cameras.append(onvif)
            self.videos.append(video)
            await onvif.start()
            LOG.debug("Camera %s ONVIF listener ready on TCP %d", c.id, c.onvif_port)
            self.tasks.append(asyncio.create_task(self.video_worker(video), name="video-" + c.id))
        self.tasks.append(asyncio.create_task(self.watch_media(enabled), name="media-watchdog"))
        await self.changed()
        try:
            self.discovery = await Discovery.start(self.cameras, self.settings.discovery_interface)
            LOG.debug("WS-Discovery ready on UDP 3702 interface=%s", self.settings.discovery_interface)
        except OSError:
            self.errors.append(
                "WS-Discovery could not bind UDP 3702; ONVIF manual adoption is available. Check interface and competing services."
            )

    async def video_worker(self, video):
        while True:
            try:
                await video.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.debug("Camera %s video worker failed: %s", video.camera.id, type(exc).__name__)
                video.error = "Video runtime interrupted; retrying. Check tablet credentials and encoder availability."
                video.jpeg = b""
                LOG.warning("Video worker interrupted; retrying without logging source credentials")
            await asyncio.sleep(5)

    async def watch_media(self, cameras):
        while True:
            await self.media.process.wait()
            LOG.warning("RTSP runtime exited; attempting recovery")
            while not self.media.running:
                await asyncio.sleep(3)
                try:
                    await self.media.start(cameras)
                except RuntimeError:
                    continue

    async def stop(self):
        if self.discovery:
            self.discovery.close()
        self.discovery = None
        tasks = self.tasks + ([self.ha_task] if self.ha_task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks = []
        self.ha_task = None
        for c in self.cameras:
            await c.stop()
        if self.media:
            await self.media.stop()
        self.media = None
        self.cameras = []
        self.videos = []
        self.errors = []

    async def apply(self, settings):
        async with self.lock:
            old = self.settings
            await self.stop()
            self.settings = settings
            for camera in settings.cameras:
                register_camera(camera)
            try:
                await self.start()
                self.store.save(settings)
            except Exception:
                await self.stop()
                self.settings = old
                await self.start()
                raise ValueError(
                    "Settings could not be applied; previous configuration restored. Check port conflicts and video runtime."
                ) from None

    def status(self):
        states = []
        for camera, video in zip(self.cameras, self.videos):
            camera.events.expire()
            states.append(
                {
                    "id": camera.camera.id,
                    "name": camera.camera.name,
                    "onvif_url": camera.base + "/onvif/device_service",
                    "motion": camera.events.motion,
                    "motion_available": camera.events.available,
                    "subscriptions": len(camera.events.subscriptions),
                    "motion_transitions": camera.events.transitions,
                    "worker_running": any(
                        t.get_name() == "video-" + camera.camera.id and not t.done() for t in self.tasks
                    ),
                    **video.status(),
                }
            )
        return {
            "ha_connected": bool(self.ha and self.ha.connected),
            "ha_error": self.ha.error if self.ha else "Starting",
            "rtsp_running": bool(self.media and self.media.running),
            "discovery_running": self.discovery is not None,
            "errors": self.errors,
            "cameras": states,
        }

    def preview_cookie(self):
        expires = str(int(time.time()) + 900)
        signature = hmac.new(self.store.admin_token.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return expires + "." + signature

    def valid_preview_cookie(self, value):
        try:
            expires, signature = value.split(".", 1)
            expected = hmac.new(self.store.admin_token.encode(), expires.encode(), hashlib.sha256).hexdigest()
            return 0 < int(expires) - time.time() <= 900 and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    def admin_app(self):
        @web.middleware
        async def access(request, handler):
            if self.supervisor:
                if request.remote != "172.30.32.2":
                    LOG.debug("Ingress request rejected: peer=%s path=%s", request.remote, request.path)
                    raise web.HTTPForbidden(text="Use Home Assistant ingress")
            elif request.path.startswith("/api/"):
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                valid_preview = request.path.startswith("/api/live/") and self.valid_preview_cookie(
                    request.cookies.get("tablet_onvif_preview", "")
                )
                if not valid_preview and not hmac.compare_digest(token.encode(), self.store.admin_token.encode()):
                    LOG.debug("Admin request unauthorized: path=%s", request.path)
                    raise web.HTTPUnauthorized()
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:"
                )
                return response
            except (ValueError, TypeError) as exc:
                # Validation errors are controlled messages; avoid reflecting credentials from constructors.
                return web.json_response(
                    {"error": str(exc) if isinstance(exc, ValueError) else "Invalid settings format"}, status=400
                )

        app = web.Application(middlewares=[access], client_max_size=262144)

        async def index(request):
            return web.FileResponse(Path(__file__).parent / "static/index.html")

        async def config(request):
            return web.json_response(
                {
                    "ha_url": self.settings.ha_url,
                    "rtsp_port": self.settings.rtsp_port,
                    "discovery_interface": self.settings.discovery_interface,
                    "cameras": [c.public() for c in self.settings.cameras],
                    "ingress": self.supervisor,
                }
            )

        async def status(request):
            return web.json_response(self.status())

        async def tablets(request):
            return web.json_response(self.ha.tablets() if self.ha else [])

        async def save(request):
            # No destructive action through cross-origin forms; require JSON + custom header.
            if request.content_type != "application/json" or request.headers.get("X-Tablet-ONVIF") != "1":
                raise web.HTTPForbidden()
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ValueError("Settings must be an object")
            previous = {c.id: c for c in self.settings.cameras}
            for c in raw.get("cameras", []):
                for field in ("password", "source_password"):
                    if not c.get(field) and c.get("id") in previous:
                        c[field] = getattr(previous[c["id"]], field)
                    c.pop(field + "_set", None)
            raw.pop("ingress", None)
            await self.apply(Settings.parse(raw))
            return web.json_response({"saved": True})

        async def setup_camera(request):
            if request.content_type != "application/json" or request.headers.get("X-Tablet-ONVIF") != "1":
                raise web.HTTPForbidden()
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid setup request")
            async with self.setup_lock:
                settings, camera = await prepare_camera(self.settings, self.ha, payload)
                await self.apply(settings)
            return web.json_response({"camera": camera.public()})

        async def connection(request):
            camera = next((c for c in self.settings.cameras if c.id == request.match_info["id"]), None)
            if camera is None:
                raise web.HTTPNotFound()
            # Explicit authenticated request only; never included in ordinary config/status polling.
            return web.json_response(
                {
                    "name": camera.name,
                    "host": camera.advertise_host,
                    "port": camera.onvif_port,
                    "url": f"http://{camera.advertise_host}:{camera.onvif_port}/onvif/device_service",
                    "username": camera.username,
                    "password": camera.password,
                }
            )

        async def preview(request):
            video = next((v for v in self.videos if v.camera.id == request.match_info["id"]), None)
            if not video or not video.status()["video_available"]:
                raise web.HTTPServiceUnavailable(text="No live frame available")
            return web.Response(body=video.jpeg, content_type="image/jpeg")

        async def preview_session(request):
            if request.headers.get("X-Tablet-ONVIF") != "1":
                raise web.HTTPForbidden()
            response = web.json_response({"ready": True})
            if not self.supervisor:
                response.set_cookie(
                    "tablet_onvif_preview",
                    self.preview_cookie(),
                    max_age=900,
                    httponly=True,
                    samesite="Strict",
                    secure=request.secure,
                    path="/api/live/",
                )
            return response

        async def live(request):
            LOG.debug("Preview request accepted: path=%s peer=%s", request.path, request.remote)
            camera = next((c for c in self.settings.cameras if c.id == request.match_info["id"] and c.enabled), None)
            if camera is None or self.ha is None:
                return web.json_response({"error": "Camera not configured or Home Assistant is starting."}, status=404)
            video = next((v for v in self.videos if v.camera.id == camera.id), None)
            if video is None:
                # Source preview also works when the RTSP encoder/server hasn't started.
                video = Video(camera, self.ha, self.settings.rtsp_port, self.store.directory)
            return await video.relay(request, check_only=request.path.startswith("/api/source/"))

        app.router.add_get("/", index)
        app.router.add_static("/static", Path(__file__).parent / "static")
        app.router.add_get("/api/config", config)
        app.router.add_put("/api/config", save)
        app.router.add_get("/api/status", status)
        app.router.add_get("/api/tablets", tablets)
        app.router.add_post("/api/setup", setup_camera)
        app.router.add_get("/api/connection/{id}", connection)
        app.router.add_get("/api/preview/{id}", preview)
        app.router.add_post("/api/preview-session", preview_session)
        app.router.add_get("/api/source/{id}", live)
        app.router.add_get("/api/live/{id}", live)
        return app


async def main():
    directory = Path(os.environ.get("DATA_DIR", "/data"))
    configure_logging(debug_option())
    bridge = Bridge(Store(directory))
    runner = web.AppRunner(bridge.admin_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(
        runner, "0.0.0.0" if bridge.supervisor else os.environ.get("ADMIN_HOST", "127.0.0.1"), 8099
    ).start()
    try:
        try:
            await bridge.start()
        except Exception as exc:
            LOG.debug("Bridge startup failure: %s", type(exc).__name__)
            bridge.errors.append(
                "Bridge runtime could not start; inspect camera settings and port conflicts, then save settings to retry."
            )
            LOG.error("Bridge runtime startup failed; setup interface remains available")
        LOG.info("Setup interface ready on port 8099; credentials are never logged")
        stopped = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
        await stopped.wait()
    finally:
        await bridge.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
