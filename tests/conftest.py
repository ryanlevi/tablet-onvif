import pytest
from bridge.config import Camera


@pytest.fixture
def camera():
    return Camera(
        id="wall_tablet",
        name="Garage tablet",
        stream_entity="sensor.tablet_camera_stream",
        motion_entity="binary_sensor.tablet_camera_motion",
        advertise_host="192.168.1.80",
        onvif_port=8101,
        username="protect_camera",
        password="test-long-password",
    )
