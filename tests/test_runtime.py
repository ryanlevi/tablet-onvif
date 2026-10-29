import asyncio
import json
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bridge.ha import HomeAssistant
from bridge.media import MediaServer, Video


async def test_actual_ha_websocket_state_lifecycle():
    subscribed = asyncio.Event()
    connected = asyncio.Event()
    disconnected = asyncio.Event()
    state_on = asyncio.Event()
    requests = []
    close = asyncio.Event()
    ha = None

    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        credentials = await ws.receive_json()
        assert credentials["access_token"] == "fixture-token"
        await ws.send_json({"type": "auth_ok"})
        for _ in range(6):
            item = await ws.receive_json()
            requests.append(item)
            result = [{"entity_id": "binary_sensor.motion", "state": "off"}] if item["type"] == "get_states" else []
            await ws.send_json({"id": item["id"], "type": "result", "success": True, "result": result})
        subscribed.set()
        await ws.send_json(
            {
                "id": 1,
                "type": "event",
                "event": {
                    "data": {
                        "entity_id": "binary_sensor.motion",
                        "new_state": {"entity_id": "binary_sensor.motion", "state": "on"},
                    }
                },
            }
        )
        await close.wait()
        await ws.close()
        return ws

    async def changed():
        if ha.connected:
            connected.set()
        if ha.state("binary_sensor.motion") == "on":
            state_on.set()
        if subscribed.is_set() and not ha.connected:
            disconnected.set()

    app = web.Application()
    app.router.add_get("/api/websocket", websocket)
    async with TestServer(app) as server:
        ha = HomeAssistant(str(server.make_url("")).rstrip("/"), "fixture-token", changed)
        task = asyncio.create_task(ha.run())
        try:
            await asyncio.wait_for(state_on.wait(), 2)
            assert requests[0]["type"] == "subscribe_events"
            assert ha.state("binary_sensor.motion") == "on"
            close.set()
            await asyncio.wait_for(disconnected.wait(), 2)
            assert ha.state("binary_sensor.motion") == "unavailable"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_mjpeg_proxy_attaches_source_auth_without_redirecting(camera, tmp_path):
    requests = []

    async def source(request):
        requests.append(request.headers.get("Authorization"))
        return web.Response(body=b"fake-mjpeg", headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"})

    app = web.Application()
    app.router.add_get("/camera", source)
    async with TestServer(app) as source_server:
        # Explicit administrator-selected source for this loopback test only.
        v = Video(camera, SimpleNamespace(), 8556, tmp_path)
        v.source = lambda: str(source_server.make_url("/camera"))
        camera.source_username, camera.source_password = "tablet_user", "source-private-password"
        v.session = aiohttp.ClientSession()
        proxy_app = web.Application()
        proxy_app.router.add_get("/{key}", v.proxy)
        try:
            async with TestClient(TestServer(proxy_app)) as client:
                r = await client.get("/wrong")
                assert r.status == 403
                r = await client.get("/" + v.proxy_key)
                assert await r.read() == b"fake-mjpeg"
                assert requests == [aiohttp.BasicAuth(camera.source_username, camera.source_password).encode()]
        finally:
            await v.session.close()


async def test_mediamtx_configuration_enforces_remote_read_only(camera, tmp_path, monkeypatch):
    processes = []

    async def spawn(*args, **kwargs):
        processes.append(args)

        class Process:
            returncode = None

            def terminate(self):
                self.returncode = 0

            async def wait(self):
                return self.returncode

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    media = MediaServer(tmp_path, 8556)
    await media.start([camera])
    cfg = json.loads((tmp_path / "mediamtx.json").read_text())
    assert cfg["rtspTransports"] == ["tcp"]
    assert cfg["api"] is False
    publisher, reader = cfg["authInternalUsers"]
    assert publisher["ips"] == ["127.0.0.1/32", "::1/128"]
    assert all(p["action"] == "read" for p in reader["permissions"])
    assert {p["path"] for p in reader["permissions"]} == {"wall_tablet_main", "wall_tablet_sub"}
    assert camera.password not in str(processes)
    await media.stop()
