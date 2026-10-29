from datetime import datetime, timezone
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web

from bridge.onvif import NS, Onvif, duration
from test_auth import token


def soap(operation, credentials=None):
    root = ET.Element(f"{{{NS['s']}}}Envelope")
    if credentials is not None:
        header = ET.SubElement(root, f"{{{NS['s']}}}Header")
        header.append(credentials[0])
    body = ET.SubElement(root, f"{{{NS['s']}}}Body")
    body.append(operation)
    return ET.tostring(root)


@pytest.fixture
def server(camera):
    video = SimpleNamespace(
        rtsp=lambda profile, external=False: f"rtsp://192.168.1.80:8556/wall_tablet_{profile}",
        jpeg=b"jpeg",
        status=lambda: {"video_available": True},
    )
    return Onvif(camera, "83f8a0ee-f9f9-49d3-9f0f-c4bb16c42b5d", video)


@pytest.mark.parametrize(
    "name,prefix",
    [
        ("GetCapabilities", "tds"),
        ("GetServices", "tds"),
        ("GetDeviceInformation", "tds"),
        ("GetNetworkInterfaces", "tds"),
        ("GetScopes", "tds"),
        ("GetProfiles", "trt"),
        ("GetVideoSources", "trt"),
        ("GetVideoEncoderConfigurations", "trt"),
        ("GetVideoEncoderConfigurationOptions", "trt"),
        ("GetEventProperties", "tev"),
    ],
)
async def test_responses_are_well_formed_and_have_no_secrets(server, name, prefix):
    op = ET.Element(f"{{{NS[prefix]}}}{name}")
    response = await server.dispatch(name, op, f"/onvif/{'events' if prefix == 'tev' else 'media'}_service")
    http = server.response(None, response)
    doc = ET.fromstring(http.body)
    assert doc.find(f"s:Body/{prefix}:{name}Response", NS) is not None
    assert server.camera.password.encode() not in http.body


async def test_subscription_lifecycle_and_motion_xml(server):
    create = ET.Element(f"{{{NS['tev']}}}CreatePullPointSubscription")
    result = await server.dispatch("CreatePullPointSubscription", create, "/onvif/events_service")
    address = result.findtext("tev:SubscriptionReference/wsa:Address", namespaces=NS)
    path = "/onvif/subscriptions/" + address.rsplit("/", 1)[-1]
    server.events.update("on")
    pull = ET.fromstring(
        f'<tev:PullMessages xmlns:tev="{NS["tev"]}"><tev:Timeout>PT0S</tev:Timeout><tev:MessageLimit>10</tev:MessageLimit></tev:PullMessages>'
    )
    messages = await server.dispatch("PullMessages", pull, path)
    body = server.response(None, messages).body
    doc = ET.fromstring(body)
    items = doc.findall(".//tt:Data/tt:SimpleItem", NS)
    assert [i.get("Value") for i in items] == ["false", "true"]
    assert b"CellMotionDetector/Motion" in body
    renew = ET.Element(f"{{{NS['wsnt']}}}Renew")
    await server.dispatch("Renew", renew, path)
    await server.dispatch("Unsubscribe", ET.Element("Unsubscribe"), path)
    assert not server.events.subscriptions


async def test_real_http_wsse_auth_not_basic_gate(server):
    app = web.Application()
    app.router.add_post("/onvif/device_service", server.soap)
    async with TestClient(TestServer(app)) as client:
        op = ET.Element(f"{{{NS['tds']}}}GetDeviceInformation")
        r = await client.post("/onvif/device_service", data=soap(op))
        assert r.status == 401
        cred = token(server.camera.username, server.camera.password, stamp=datetime.now(timezone.utc).timestamp())
        payload = soap(op, cred)
        r = await client.post("/onvif/device_service", data=payload)
        assert r.status == 200
        r = await client.post("/onvif/device_service", data=payload)
        assert r.status == 401  # replay rejected
        r = await client.post(
            "/onvif/device_service",
            data=soap(op),
            auth=aiohttp.BasicAuth(server.camera.username, server.camera.password),
        )
        assert r.status == 200
        r = await client.post("/onvif/device_service", data=soap(ET.Element(f"{{{NS['tds']}}}GetSystemDateAndTime")))
        assert r.status == 200
        r = await client.post("/onvif/device_service", data=b'<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>')
        assert r.status == 400


@pytest.mark.parametrize("text,expected", [("PT30S", 30), ("PT1M", 60), ("PT99H", 3600)])
def test_durations(text, expected):
    assert duration(text) == expected


async def test_wsa_request_action_maps_to_response_action(server):
    request = ET.fromstring(
        f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:a="{NS["wsa"]}"><s:Header><a:Action>http://www.onvif.org/ver10/events/wsdl/PullMessagesRequest</a:Action><a:MessageID>urn:uuid:test</a:MessageID></s:Header><s:Body/></s:Envelope>'
    )
    response = server.response(request, ET.Element(f"{{{NS['tev']}}}PullMessagesResponse"))
    doc = ET.fromstring(response.body)
    assert doc.findtext("s:Header/wsa:Action", namespaces=NS).endswith("/PullMessagesResponse")
    assert doc.findtext("s:Header/wsa:RelatesTo", namespaces=NS) == "urn:uuid:test"
