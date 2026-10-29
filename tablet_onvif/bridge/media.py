from __future__ import annotations

import asyncio
import json
import secrets

from .config import Store, validate_source


class MediaServer:
    def __init__(self, directory, port):
        self.directory, self.port = directory, port
        self.process = None

    async def start(self, cameras):
        users = [
            {
                "user": "any",
                "pass": "",
                "ips": ["127.0.0.1/32", "::1/128"],
                "permissions": [{"action": "publish"}, {"action": "read"}],
            }
        ]
        for c in cameras:
            if c.enabled:
                users.append(
                    {
                        "user": c.username,
                        "pass": c.password,
                        "ips": [],
                        "permissions": [
                            {"action": "read", "path": c.id + "_" + profile} for profile in ("main", "sub")
                        ],
                    }
                )
        cfg = {
            "logLevel": "warn",
            "logDestinations": ["stdout"],
            "api": False,
            "metrics": False,
            "pprof": False,
            "playback": False,
            "rtmp": False,
            "hls": False,
            "webrtc": False,
            "srt": False,
            "rtspAddress": f":{self.port}",
            "rtspTransports": ["tcp"],
            "rtspAuthMethods": ["basic", "digest"],
            "authMethod": "internal",
            "authInternalUsers": users,
            "paths": {c.id + "_" + p: {"source": "publisher"} for c in cameras if c.enabled for p in ("main", "sub")},
        }
        path = self.directory / "mediamtx.json"
        Store.write(path, json.dumps(cfg))
        self.process = await asyncio.create_subprocess_exec(
            "mediamtx",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.directory,
        )
        await asyncio.sleep(0.3)
        if self.process.returncode is not None:
            raise RuntimeError("RTSP server failed to start; check port conflicts and MediaMTX version")

    async def stop(self):
        await terminate(self.process)
        self.process = None

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None


async def terminate(process):
    if process and process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


class Video:
    def __init__(self, camera, ha, rtsp_port, directory):
        self.camera, self.ha, self.port, self.directory = camera, ha, rtsp_port, directory
        self.task = None
        self.error = "Waiting for source"
        self.active_url = ""
        self.last_frame = 0.0
        self.restarts = 0
        self.process = None
        self.proxy_port = None
        self.proxy_key = secrets.token_urlsafe(32)
        self.jpeg = b""
        self.session = None

    def source(self):
        c = self.camera
        if self.ha.state(c.stream_entity) in ("unavailable", "unknown"):
            return ""
        value = c.source_url or self.ha.stream_url(c.stream_entity)
        if value:
            validate_source(value)
        return value

    def rtsp(self, profile="main", external=False):
        host = self.camera.advertise_host if external else "127.0.0.1"
        return f"rtsp://{host}:{self.port}/{self.camera.id}_{profile}"

    async def proxy(self, request):
        """Loopback-only relay for FFmpeg; browser access uses the authenticated admin route."""
        from aiohttp import web

        if request.remote not in ("127.0.0.1", "::1") or request.match_info["key"] != self.proxy_key:
            raise web.HTTPForbidden()
        return await self.relay(request)

    async def relay(self, request, check_only=False):
        """Same-origin live MJPEG. Source credentials and redirects never reach the browser."""
        import aiohttp
        from aiohttp import web

        try:
            source = self.source()
        except ValueError:
            return web.json_response(
                {"error": "The tablet Stream URL is invalid. Check the source URL override."}, status=503
            )
        if not source:
            return web.json_response(
                {
                    "error": "No active tablet stream. Enable Camera Stream and keep the Companion App open in kiosk mode."
                },
                status=503,
            )
        auth = (
            aiohttp.BasicAuth(self.camera.source_username, self.camera.source_password)
            if self.camera.source_username
            else None
        )
        response = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    source,
                    auth=auth,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=20),
                ) as upstream:
                    if upstream.status != 200:
                        if upstream.status in (401, 403):
                            error = "The iPad rejected its stream credentials. Check the source username and password, not the NVR credentials."
                        elif 300 <= upstream.status < 400:
                            error = (
                                "The source redirected to another address. Set the final tablet camera URL directly."
                            )
                        else:
                            error = f"The tablet camera returned HTTP {upstream.status}. Check the camera URL and app settings."
                        return web.json_response({"error": error}, status=502)
                    content_type = upstream.headers.get("Content-Type", "")
                    if not content_type.lower().startswith("multipart/") or "boundary=" not in content_type.lower():
                        return web.json_response(
                            {
                                "error": "The URL did not return an MJPEG camera stream. Use the Companion App's Stream URL."
                            },
                            status=502,
                        )
                    if check_only:
                        return web.json_response({"available": True})
                    response = web.StreamResponse(
                        headers={
                            "Content-Type": content_type,
                            "Cache-Control": "no-store",
                            "X-Accel-Buffering": "no",
                            "X-Content-Type-Options": "nosniff",
                            "Referrer-Policy": "no-referrer",
                        }
                    )
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_chunked(65536):
                        await response.write(chunk)
                    return response
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionResetError):
            if response is not None and response.prepared:
                return response
            return web.json_response(
                {
                    "error": "Cannot reach the tablet camera. Check Wi-Fi/VLAN access, stream port and that the Companion App is in the foreground."
                },
                status=502,
            )

    async def run(self):
        import aiohttp
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/{key}", self.proxy)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.proxy_port = site._server.sockets[0].getsockname()[1]
        self.session = aiohttp.ClientSession()
        snapshot = self.directory / f"{self.camera.id}.jpg"
        snapshot.unlink(missing_ok=True)
        try:
            delay = 1
            while True:
                try:
                    source = self.source()
                except ValueError:
                    source = ""
                    self.error = "Invalid tablet source URL"
                if not source:
                    self.error = "Tablet stream unavailable; enable Camera Stream and keep Companion App in foreground"
                    self.jpeg = b""
                    await asyncio.sleep(2)
                    continue
                c = self.camera
                sub_width, sub_height = max(160, c.width // 4 * 2), max(120, c.height // 4 * 2)
                args = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-rw_timeout",
                    "20000000",
                    "-use_wallclock_as_timestamps",
                    "1",
                    "-i",
                    f"http://127.0.0.1:{self.proxy_port}/{self.proxy_key}",
                ]
                for p, width, height, bitrate in (
                    ("main", c.width, c.height, c.bitrate),
                    ("sub", sub_width, sub_height, max(150, c.bitrate // 4)),
                ):
                    args += [
                        "-map",
                        "0:v:0",
                        "-an",
                        "-vf",
                        f"fps={c.fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-preset",
                        "veryfast",
                        "-tune",
                        "zerolatency",
                        "-profile:v",
                        "main",
                        "-b:v",
                        f"{bitrate}k",
                        "-g",
                        str(c.fps * 2),
                        "-f",
                        "rtsp",
                        "-rtsp_transport",
                        "tcp",
                        self.rtsp(p),
                    ]
                args += [
                    "-map",
                    "0:v:0",
                    "-an",
                    "-vf",
                    "fps=1,scale=640:-2",
                    "-update",
                    "1",
                    "-atomic_writing",
                    "1",
                    "-f",
                    "image2",
                    str(snapshot),
                ]
                self.process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                self.active_url = source
                self.error = "Starting video encoder"
                started = asyncio.get_running_loop().time()
                try:
                    previous = 0
                    while self.process.returncode is None:
                        await asyncio.sleep(1)
                        now = asyncio.get_running_loop().time()
                        if snapshot.exists() and snapshot.stat().st_mtime_ns != previous:
                            previous = snapshot.stat().st_mtime_ns
                            self.jpeg = snapshot.read_bytes()
                            self.last_frame = now
                            self.error = ""
                        if self.source() != source or now - max(self.last_frame, started) > 30:
                            break
                finally:
                    await terminate(self.process)
                    self.process = None
                    self.active_url = ""
                    self.jpeg = b""
                    self.restarts += 1
                    snapshot.unlink(missing_ok=True)
                self.error = (
                    "Video interrupted; retrying (check tablet camera, source credentials and LAN reachability)"
                )
                delay = 1 if asyncio.get_running_loop().time() - started > 30 else min(delay * 2, 30)
                await asyncio.sleep(delay)
        finally:
            await terminate(self.process)
            await self.session.close()
            await runner.cleanup()
            snapshot.unlink(missing_ok=True)

    def status(self):
        now = asyncio.get_running_loop().time()
        live = bool(self.jpeg and self.process and self.process.returncode is None and now - self.last_frame < 10)
        return {
            "video_available": live,
            "error": self.error,
            "restarts": self.restarts,
            "last_frame_age": round(now - self.last_frame, 1) if self.last_frame else None,
        }
