from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bridge.app import Bridge
from bridge.config import Settings, Store
from bridge.setup import prepare_camera


def ha():
    return SimpleNamespace(
        connected=True,
        tablets=lambda: [
            {
                "name": "Garage iPad",
                "stream_entity": "sensor.garage_camera_stream",
                "motion_entity": "binary_sensor.garage_camera_motion",
            }
        ],
        state=lambda e: "idle",
        stream_url=lambda e: "http://192.0.2.60:8090/camera",
    )


@pytest.fixture
def automatic(monkeypatch):
    async def detect(url):
        return "192.0.2.10"

    monkeypatch.setattr("bridge.setup.detect_host", detect)
    monkeypatch.setattr("bridge.setup.free_port", lambda reserved: 8102 if 8101 in reserved else 8101)


async def test_setup_only_needs_a_discovered_tablet(automatic):
    settings, c = await prepare_camera(
        Settings(), ha(), {"stream_entity": "sensor.garage_camera_stream", "source_auth": False}
    )
    assert c.name == "Garage iPad" and c.id == "garage"
    assert c.advertise_host == "192.0.2.10" and c.onvif_port == 8101
    assert len(c.password) >= 24 and c.username == "nvr_garage"
    assert c.motion_entity == "binary_sensor.garage_camera_motion"
    assert settings.cameras[0] == c


async def test_edit_preserves_identity_credentials_and_tuning(camera):
    camera.stream_entity = "sensor.garage_camera_stream"
    camera.width = 1920
    settings, c = await prepare_camera(
        Settings(cameras=[camera]), ha(), {"stream_entity": camera.stream_entity, "name": "New name"}
    )
    assert c.id == camera.id and c.password == camera.password and c.username == camera.username
    assert c.onvif_port == camera.onvif_port and c.width == 1920 and c.advertise_host == camera.advertise_host
    assert len(settings.cameras) == 1 and camera.name != "New name"


async def test_source_password_is_optional_but_not_silently_reused_for_new_username(camera):
    camera.stream_entity = "sensor.garage_camera_stream"
    camera.source_username = "admin"
    camera.source_password = "private-source-password"
    settings = Settings(cameras=[camera])
    _, c = await prepare_camera(
        settings,
        ha(),
        {"stream_entity": camera.stream_entity, "source_auth": True, "source_username": "admin", "source_password": ""},
    )
    assert c.source_password == camera.source_password
    with pytest.raises(ValueError, match="password"):
        await prepare_camera(
            settings,
            ha(),
            {
                "stream_entity": camera.stream_entity,
                "source_auth": True,
                "source_username": "another_user",
                "source_password": "",
            },
        )
    _, c = await prepare_camera(settings, ha(), {"stream_entity": camera.stream_entity, "source_auth": False})
    assert c.source_username == c.source_password == ""


async def test_unavailable_tablet_gives_actionable_message():
    h = ha()
    h.state = lambda e: "unavailable"
    with pytest.raises(ValueError, match="Enable Camera Stream"):
        await prepare_camera(Settings(), h, {"stream_entity": "sensor.garage_camera_stream"})
    h.connected = False
    with pytest.raises(ValueError, match="Home Assistant"):
        await prepare_camera(Settings(), h, {})


async def test_simple_setup_api_redacts_secrets_and_connection_requires_admin(automatic, tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    b = Bridge(Store(tmp_path))
    b.ha = ha()

    async def apply(settings):
        b.settings = settings

    b.apply = apply
    async with TestClient(TestServer(b.admin_app())) as client:
        headers = {"Authorization": "Bearer " + b.store.admin_token, "X-Tablet-ONVIF": "1"}
        response = await client.post(
            "/api/setup", json={"stream_entity": "sensor.garage_camera_stream", "source_auth": False}, headers=headers
        )
        assert response.status == 200
        saved = await response.json()
        assert saved["camera"]["password"] == "" and saved["camera"]["password_set"]
        response = await client.get("/api/connection/garage")
        assert response.status == 401
        response = await client.get("/api/connection/garage", headers=headers)
        details = await response.json()
        assert details["password"] == b.settings.cameras[0].password
        assert response.headers["Cache-Control"] == "no-store"
        response = await client.get("/api/config", headers=headers)
        assert details["password"] not in await response.text()


async def test_sequential_setup_does_not_overwrite_existing_camera(automatic):
    settings = Settings()
    settings, first = await prepare_camera(settings, ha(), {"stream_entity": "sensor.garage_camera_stream"})
    h = ha()
    h.tablets = lambda: [
        {
            "name": "Second tablet",
            "stream_entity": "sensor.second_camera_stream",
            "motion_entity": "binary_sensor.second_camera_motion",
        }
    ]
    settings, second = await prepare_camera(settings, h, {"stream_entity": "sensor.second_camera_stream"})
    assert first.id != second.id and first.username != second.username and first.password != second.password
    assert len(settings.cameras) == 2 and second.onvif_port != first.onvif_port


async def test_new_camera_reuses_incoming_login_even_when_short(automatic):
    settings, c = await prepare_camera(
        Settings(),
        ha(),
        {
            "stream_entity": "sensor.garage_camera_stream",
            "source_auth": True,
            "source_username": "admin",
            "source_password": "123456",
        },
    )
    assert c.username == c.source_username == "admin"
    assert c.password == c.source_password == "123456"
    settings.validate()


async def test_linked_login_follows_source_rotation_and_stays_authenticated(automatic):
    settings, c = await prepare_camera(
        Settings(),
        ha(),
        {
            "stream_entity": "sensor.garage_camera_stream",
            "source_auth": True,
            "source_username": "admin",
            "source_password": "123456",
        },
    )
    settings, c = await prepare_camera(
        settings,
        ha(),
        {
            "stream_entity": c.stream_entity,
            "source_auth": True,
            "source_username": "admin",
            "source_password": "changed",
        },
    )
    assert c.password == c.source_password == "changed"
    _, c = await prepare_camera(settings, ha(), {"stream_entity": c.stream_entity, "source_auth": False})
    assert not c.source_password and len(c.password) >= 24 and c.username.startswith("nvr_")


async def test_reused_username_can_have_different_path_scoped_passwords(automatic):
    settings, first = await prepare_camera(
        Settings(),
        ha(),
        {
            "stream_entity": "sensor.garage_camera_stream",
            "source_auth": True,
            "source_username": "admin",
            "source_password": "first",
        },
    )
    h = ha()
    h.tablets = lambda: [
        {
            "name": "Second",
            "stream_entity": "sensor.second_camera_stream",
            "motion_entity": "binary_sensor.second_camera_motion",
        }
    ]
    settings, second = await prepare_camera(
        settings,
        h,
        {
            "stream_entity": "sensor.second_camera_stream",
            "source_auth": True,
            "source_username": "admin",
            "source_password": "second",
        },
    )
    assert first.username == second.username and first.password != second.password
    settings.validate()


async def test_reserved_anonymous_username_cannot_bypass_nvr_auth(automatic):
    with pytest.raises(ValueError, match="reserved"):
        await prepare_camera(
            Settings(),
            ha(),
            {
                "stream_entity": "sensor.garage_camera_stream",
                "source_auth": True,
                "source_username": "any",
                "source_password": "secret",
            },
        )
