import asyncio
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bridge.app import Bridge
from bridge.config import Settings, Store
from bridge.media import Video

FIXTURES = Path(__file__).parent / "fixtures"


async def test_live_stream_delivers_successive_jpegs_with_preview_only_cookie(camera, tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    requests = []
    red, blue = (FIXTURES / "red.jpg").read_bytes(), (FIXTURES / "blue.jpg").read_bytes()

    async def camera_stream(request):
        requests.append(request.headers.get("Authorization"))
        response = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"})
        await response.prepare(request)
        try:
            for frame in (red, blue):
                await response.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
                await asyncio.sleep(0.01)
        except ConnectionResetError:
            pass
        return response

    source_app = web.Application()
    source_app.router.add_get("/camera", camera_stream)
    async with TestServer(source_app) as upstream:
        bridge = Bridge(Store(tmp_path))
        bridge.settings = Settings(cameras=[camera])
        bridge.ha = SimpleNamespace()
        video = Video(camera, bridge.ha, 8556, tmp_path)
        # Synthetic fixture only: production rejects localhost camera URLs.
        video.source = lambda: str(upstream.make_url("/camera"))
        camera.source_username, camera.source_password = "ipad", "private-source-password"
        bridge.videos = [video]
        async with TestClient(TestServer(bridge.admin_app())) as client:
            headers = {"Authorization": "Bearer " + bridge.store.admin_token}
            r = await client.get("/api/live/wall_tablet")
            assert r.status == 401
            r = await client.get("/api/source/wall_tablet", headers=headers)
            assert await r.json() == {"available": True}
            r = await client.post("/api/preview-session", headers={**headers, "X-Tablet-ONVIF": "1"})
            assert r.status == 200
            cookie = r.cookies["tablet_onvif_preview"]
            assert cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/api/live/"
            cookie_headers = {"Cookie": "tablet_onvif_preview=" + cookie.value}
            r = await client.get("/api/live/wall_tablet", headers=cookie_headers)
            assert r.status == 200
            multipart = aiohttp.MultipartReader.from_response(r)
            assert await (await multipart.next()).read() == red
            assert await (await multipart.next()).read() == blue
            assert r.headers["X-Accel-Buffering"] == "no"
            r.close()
            # Preview cookie never grants access to configuration or saved credentials.
            r = await client.get("/api/config", headers=cookie_headers)
            assert r.status == 401
        assert requests and all(a == aiohttp.BasicAuth("ipad", camera.source_password).encode() for a in requests)


@pytest.mark.parametrize(
    "status,content_type,expected",
    [
        (401, "text/plain", "credentials"),
        (403, "text/plain", "credentials"),
        (404, "text/plain", "404"),
        (302, "text/plain", "redirect"),
        (200, "text/html", "MJPEG"),
    ],
)
async def test_live_preview_reports_safe_source_errors(camera, tmp_path, status, content_type, expected):
    async def source(request):
        return web.Response(
            status=status,
            text="private-source-password",
            content_type=content_type,
            headers={"Location": "/secret/redirect"},
        )

    source_app = web.Application()
    source_app.router.add_get("/camera", source)
    async with TestServer(source_app) as upstream:
        video = Video(camera, SimpleNamespace(), 8556, tmp_path)
        video.source = lambda: str(upstream.make_url("/camera"))
        app = web.Application()
        app.router.add_get("/live", video.relay)
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/live")
            assert response.status == 502
            text = (await response.json())["error"]
            assert expected in text and "private-source-password" not in text


def test_expired_or_forged_preview_session_cannot_authorize(tmp_path, monkeypatch):
    bridge = Bridge(Store(tmp_path))
    cookie = bridge.preview_cookie()
    assert bridge.valid_preview_cookie(cookie)
    assert not bridge.valid_preview_cookie(cookie + "forged")
    from bridge import app as app_module

    monkeypatch.setattr(app_module.time, "time", lambda: 99999999999)
    assert not bridge.valid_preview_cookie(cookie)
