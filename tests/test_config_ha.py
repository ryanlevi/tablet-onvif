from dataclasses import asdict
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bridge.app import Bridge
from bridge.config import Settings, Store, validate_source
from bridge.ha import HomeAssistant


def test_config_storage_identity_redaction_and_ports(camera, tmp_path):
    store = Store(tmp_path)
    store.save(Settings(cameras=[camera]).validate())
    loaded = store.load()
    assert loaded.cameras[0].password == camera.password
    assert store.camera_uuid(camera) == Store(tmp_path).camera_uuid(camera)
    assert camera.public()["password"] == ""
    assert os.stat(tmp_path / "settings.json").st_mode & 0o777 == 0o600
    camera.onvif_port = 8099
    with pytest.raises(ValueError):
        Settings(cameras=[camera]).validate()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@192.168.1.8/camera",
        "http://127.0.0.1:8099/api/config",
        "http://0.0.0.0/camera",
        "http://192.168.1.8/camera#frag",
    ],
)
def test_source_validation(url):
    with pytest.raises(ValueError):
        validate_source(url)


async def test_ha_registry_device_pairing_not_name_guessing():
    async def changed():
        pass

    ha = HomeAssistant("http://supervisor/core", "secret", changed)
    assert ha.websocket_url == "ws://supervisor/core/websocket"
    ha.entities = {
        "sensor.arbitrary": {"platform": "mobile_app", "original_name": "Camera Stream", "device_id": "one"},
        "binary_sensor.renamed": {"original_name": "Camera Motion", "device_id": "one"},
        "binary_sensor.wrong_camera_motion": {"original_name": "Camera Motion", "device_id": "two"},
    }
    ha.devices = {"one": {"name": "My iPad"}}
    result = ha.tablets()
    assert result[0]["motion_entity"] == "binary_sensor.renamed"
    assert result[0]["name"] == "My iPad"
    assert HomeAssistant("https://ha.example", "secret", changed).websocket_url == "wss://ha.example/api/websocket"


async def test_admin_requires_token_and_never_returns_password(camera, tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    bridge = Bridge(Store(tmp_path))
    bridge.settings = Settings(cameras=[camera])
    async with TestClient(TestServer(bridge.admin_app())) as client:
        r = await client.get("/api/config")
        assert r.status == 401
        r = await client.get("/api/config", headers={"Authorization": "Bearer " + bridge.store.admin_token})
        assert r.status == 200
        assert camera.password not in await r.text()
        r = await client.put(
            "/api/config", json=asdict(bridge.settings), headers={"Authorization": "Bearer " + bridge.store.admin_token}
        )
        assert r.status == 403  # missing anti-CSRF/custom header


async def test_ingress_rejects_forged_headers_from_non_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "secret")
    bridge = Bridge(Store(tmp_path))
    async with TestClient(TestServer(bridge.admin_app())) as client:
        r = await client.get("/api/config", headers={"X-Ingress-Path": "/forged", "X-Forwarded-For": "172.30.32.2"})
        assert r.status == 403
