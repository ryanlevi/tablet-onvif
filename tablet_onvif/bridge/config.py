from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Camera:
    id: str
    name: str
    stream_entity: str
    motion_entity: str
    advertise_host: str
    onvif_port: int
    username: str
    password: str
    source_url: str = ""
    source_username: str = ""
    source_password: str = ""
    width: int = 1280
    height: int = 720
    fps: int = 15
    bitrate: int = 1800
    enabled: bool = True

    def validate(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.id):
            raise ValueError("Camera ID must start with a letter and contain lowercase letters, digits or underscores")
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("Camera name is required (up to 100 characters)")
        host = ipaddress.IPv4Address(self.advertise_host)
        if host.is_loopback or host.is_unspecified or host.is_multicast or host.is_link_local:
            raise ValueError("Advertise host must be a reachable LAN IPv4 address")
        if not 1024 <= self.onvif_port <= 65535:
            raise ValueError("ONVIF port must be between 1024 and 65535")
        shared_login = bool(
            self.source_username
            and self.source_password
            and self.username == self.source_username
            and self.password == self.source_password
        )
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", self.username) or self.username == "any":
            raise ValueError("NVR username must use ASCII letters, digits, _, -, . or @; 'any' is reserved")
        if len(self.password) < (1 if shared_login else 12):
            raise ValueError("NVR password must be at least 12 characters unless reusing the tablet's existing login")
        if len(self.password) > 256:
            raise ValueError("Password is too long")
        if not self.stream_entity.startswith("sensor.") or not self.motion_entity.startswith("binary_sensor."):
            raise ValueError("Select a Camera Stream sensor and Camera Motion binary sensor")
        if self.source_url:
            validate_source(self.source_url)
        if (
            not 160 <= self.width <= 3840
            or not 120 <= self.height <= 2160
            or self.width % 2
            or self.height % 2
            or not 1 <= self.fps <= 30
            or not 100 <= self.bitrate <= 16000
        ):
            raise ValueError("Invalid video settings; dimensions must be even, FPS 1–30, bitrate 100–16000 kbps")
        return self

    def public(self):
        data = asdict(self)
        for key in ("password", "source_password"):
            data[key] = ""
            data[key + "_set"] = bool(getattr(self, key))
        return data


def validate_source(value: str):
    u = urlsplit(value)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError("Stream URL must be HTTP(S), with credentials entered separately and no URL fragment")
    # Administrator-selected LAN sources are intentional; do not allow local control endpoints.
    try:
        ip = ipaddress.ip_address(u.hostname)
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("Stream URL must reference a tablet on the LAN")
    except ValueError as exc:
        if "Stream URL" in str(exc):
            raise
    return value


@dataclass
class Settings:
    ha_url: str = "http://supervisor/core"
    rtsp_port: int = 8556
    discovery_interface: str = "0.0.0.0"
    cameras: list[Camera] = field(default_factory=list)

    def validate(self):
        u = urlsplit(self.ha_url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("Home Assistant URL must be HTTP(S), without credentials or query parameters")
        if not 1024 <= self.rtsp_port <= 65535 or self.rtsp_port == 8099:
            raise ValueError("Invalid RTSP port")
        ipaddress.IPv4Address(self.discovery_interface)
        ids, ports = set(), {8099, self.rtsp_port}
        if len(self.cameras) > 16:
            raise ValueError("Maximum 16 cameras")
        for c in self.cameras:
            c.validate()
            if c.id in ids or c.onvif_port in ports:
                raise ValueError("Camera IDs and ONVIF ports must each be unique")
            ids.add(c.id)
            ports.add(c.onvif_port)
        return self

    @classmethod
    def parse(cls, raw):
        return cls(
            ha_url=raw.get("ha_url", "http://supervisor/core"),
            rtsp_port=int(raw.get("rtsp_port", 8556)),
            discovery_interface=raw.get("discovery_interface", "0.0.0.0"),
            cameras=[Camera(**x) for x in raw.get("cameras", [])],
        ).validate()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self.identity = self.secret("installation-id", lambda: str(uuid.uuid4()))
        self.admin_token = self.secret("admin-token", lambda: os.urandom(32).hex())

    def secret(self, name, factory):
        path = self.directory / name
        if not path.exists():
            self.write(path, factory())
        return path.read_text().strip()

    def load(self):
        path = self.directory / "settings.json"
        if path.exists():
            return Settings.parse(json.loads(path.read_text()))
        return Settings(ha_url=os.environ.get("HA_URL", "http://supervisor/core"))

    def save(self, settings):
        self.write(self.directory / "settings.json", json.dumps(asdict(settings), indent=2))

    @staticmethod
    def write(path, content):
        fd, name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def camera_uuid(self, camera):
        return str(uuid.uuid5(uuid.UUID(self.identity), camera.id))
