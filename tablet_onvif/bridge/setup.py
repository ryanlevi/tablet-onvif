"""Sensible defaults for the tablet-first setup flow."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import socket
from dataclasses import asdict
from urllib.parse import urlsplit

from .config import Camera, Settings


def lan_address(value):
    try:
        address = ipaddress.IPv4Address(value)
        return not (address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local)
    except (ValueError, TypeError):
        return False


async def detect_host(source_url):
    """Ask the routing table which local interface reaches this tablet; send no packets."""
    host = urlsplit(source_url).hostname
    if not host:
        raise ValueError("Enable Camera Stream on the tablet, or enter the bridge address under Advanced.")
    try:
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM), 5
        )
        for _, _, _, _, remote in addresses:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((remote[0], 9))
                candidate = route.getsockname()[0]
            if lan_address(candidate):
                return candidate
    except (OSError, asyncio.TimeoutError):
        pass
    raise ValueError("Couldn't detect the bridge address. Enter its LAN address under Advanced.")


def free_port(reserved):
    for port in range(8101, 8201):
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("0.0.0.0", port))
                return port
            except OSError:
                pass
    raise ValueError("No free camera port found. Check Advanced settings.")


async def prepare_camera(settings, ha, payload):
    if not ha or not ha.connected:
        raise ValueError("Waiting for Home Assistant. Try again when it connects.")
    entity = payload.get("stream_entity", "")
    existing = next((c for c in settings.cameras if c.stream_entity == entity), None)
    tablet = next((t for t in ha.tablets() if t["stream_entity"] == entity), None)
    if existing is None and tablet is None:
        raise ValueError("Tablet not found. Refresh the tablet list and try again.")
    linked_login = existing is None or bool(
        existing.source_username
        and existing.source_password
        and existing.username == existing.source_username
        and existing.password == existing.source_password
    )
    if existing:
        camera = Camera(**asdict(existing))
    else:
        if not tablet.get("motion_entity"):
            raise ValueError("Enable Camera Motion in this tablet's Companion App settings first.")
        if ha.state(entity) in ("unavailable", "unknown") or not ha.stream_url(entity):
            raise ValueError("Enable Camera Stream on this tablet and keep the Companion App open.")
        base = re.sub(r"[^a-z0-9_]", "_", entity.removeprefix("sensor.").removesuffix("_camera_stream").lower())[:32]
        if not base or not base[0].isalpha():
            base = ("tablet_" + base)[:32]
        identity, number = base, 2
        while identity in {c.id for c in settings.cameras}:
            identity = f"{base}_{number}"
            number += 1
        host = payload.get("bridge_host") or await detect_host(ha.stream_url(entity))
        camera = Camera(
            id=identity,
            name=tablet["name"],
            stream_entity=entity,
            motion_entity=tablet["motion_entity"],
            advertise_host=host,
            onvif_port=free_port({8099, settings.rtsp_port, *(c.onvif_port for c in settings.cameras)}),
            username="nvr_" + identity,
            password=secrets.token_urlsafe(24),
        )
    if payload.get("bridge_host"):
        camera.advertise_host = payload["bridge_host"]
    camera.name = payload.get("name", camera.name).strip() or camera.name
    if "source_auth" in payload:
        if payload["source_auth"]:
            username = payload.get("source_username", "").strip()
            password = payload.get("source_password", "")
            if not username:
                raise ValueError("Enter the username set in the iPad's Camera Stream settings.")
            if not password and existing and username == existing.source_username:
                password = existing.source_password
            if not password:
                raise ValueError("Enter the password set in the iPad's Camera Stream settings.")
            camera.source_username, camera.source_password = username, password
        else:
            camera.source_username = camera.source_password = ""
    if linked_login:
        if camera.source_username and camera.source_password:
            camera.username, camera.password = camera.source_username, camera.source_password
        elif existing and existing.source_username:
            # Removing source authentication must not expose an unauthenticated NVR camera.
            camera.username = "nvr_" + camera.id
            camera.password = secrets.token_urlsafe(24)
    camera.enabled = True
    camera.validate()
    data = asdict(settings)
    data["cameras"] = [asdict(camera) if c.id == camera.id else asdict(c) for c in settings.cameras]
    if existing is None:
        data["cameras"].append(asdict(camera))
    return Settings.parse(data), camera
