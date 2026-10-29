import base64
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from bridge.auth import Authentication, REALM, WSSE, WSU


def token(username="camera", password="password", nonce=b"random-client-nonce", stamp=1000):
    created = datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    root = ET.Element("Envelope")
    t = ET.SubElement(root, f"{{{WSSE}}}UsernameToken")
    for name, value in (("Username", username), ("Password", digest), ("Nonce", base64.b64encode(nonce).decode())):
        e = ET.SubElement(t, f"{{{WSSE}}}{name}")
        e.text = value
        if name == "Password":
            e.set("Type", "urn:test#PasswordDigest")
    ET.SubElement(t, f"{{{WSU}}}Created").text = created
    return root


def test_wsse_valid_and_replay_rejected():
    a = Authentication("camera", "password", clock=lambda: 1000)
    t = token()
    assert a.wsse(t)
    assert not a.wsse(t)


@pytest.mark.parametrize("args", [{"password": "wrong"}, {"username": "wrong"}, {"stamp": 0}, {"stamp": 1400}])
def test_wsse_rejects_bad_credentials_and_clock(args):
    assert not Authentication("camera", "password", clock=lambda: 1000).wsse(token(**args))


def test_basic_auth():
    a = Authentication("camera", "password")
    request = SimpleNamespace(headers={"Authorization": "Basic " + base64.b64encode(b"camera:password").decode()})
    assert a.http(request)
    request.headers["Authorization"] = "Basic !!!"
    assert not a.http(request)


def test_http_digest_uri_and_replay():
    a = Authentication("camera", "password", clock=lambda: 1000)
    nonce = a.nonce()

    def md5(v):
        return hashlib.md5(v.encode()).hexdigest()

    a1, a2 = md5(f"camera:{REALM}:password"), md5("POST:/onvif/device_service")
    digest = md5(f"{a1}:{nonce}:00000001:client:auth:{a2}")
    header = f'Digest username="camera", realm="{REALM}", nonce="{nonce}", uri="/onvif/device_service", qop=auth, nc=00000001, cnonce="client", response="{digest}"'
    request = SimpleNamespace(headers={"Authorization": header}, method="POST", raw_path="/onvif/device_service")
    assert a.http(request)
    assert not a.http(request)
    request.raw_path = "/another"
    assert not a.http(request)


def test_nonce_expiration():
    now = [1000]
    a = Authentication("camera", "password", clock=lambda: now[0])
    nonce = a.nonce()
    assert a.valid_nonce(nonce)
    now[0] = 1400
    assert not a.valid_nonce(nonce)
    assert not a.valid_nonce("bad")
