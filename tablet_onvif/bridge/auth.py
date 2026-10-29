"""ONVIF WS-Security and HTTP authentication; never wrap ONVIF in UI auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone

WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
REALM = "Tablet ONVIF"


class Authentication:
    def __init__(self, username, password, clock=time.time):
        self.username, self.password, self.clock = username, password, clock
        self.key = secrets.token_bytes(32)
        self.used = {}

    def nonce(self):
        text = f"{int(self.clock())}:{secrets.token_hex(12)}"
        signature = hmac.new(self.key, text.encode(), hashlib.sha256).hexdigest()
        return base64.b64encode(f"{text}:{signature}".encode()).decode()

    def valid_nonce(self, nonce):
        try:
            stamp, random, signature = base64.b64decode(nonce, validate=True).decode().split(":")
            expected = hmac.new(self.key, f"{stamp}:{random}".encode(), hashlib.sha256).hexdigest()
            return 0 <= self.clock() - int(stamp) <= 300 and hmac.compare_digest(signature, expected)
        except (ValueError, UnicodeError):
            return False

    def claim(self, key, expiration, count=1):
        self.used = {k: v for k, v in self.used.items() if v[0] > self.clock()}
        if count <= self.used.get(key, (0, 0))[1] or len(self.used) >= 4096:
            return False
        self.used[key] = (expiration, count)
        return True

    def wsse(self, root):
        token = root.find(f".//{{{WSSE}}}UsernameToken")
        if token is None:
            return False
        try:
            username = token.findtext(f"{{{WSSE}}}Username", "")
            password = token.find(f"{{{WSSE}}}Password")
            created = token.findtext(f"{{{WSU}}}Created", "")
            nonce = token.findtext(f"{{{WSSE}}}Nonce", "")
            stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if stamp.tzinfo is None or abs(self.clock() - stamp.timestamp()) > 300:
                return False
            if password is None or not password.get("Type", "").endswith("#PasswordDigest"):
                return False
            digest = base64.b64encode(
                hashlib.sha1(
                    base64.b64decode(nonce, validate=True) + created.encode() + self.password.encode()
                ).digest()
            ).decode()
            valid = hmac.compare_digest(username, self.username) and hmac.compare_digest(password.text or "", digest)
            return valid and self.claim("wsse:" + nonce + ":" + created, self.clock() + 600)
        except (ValueError, TypeError):
            return False

    def http(self, request):
        value = request.headers.get("Authorization", "")
        if value.startswith("Basic "):
            try:
                actual = base64.b64decode(value[6:], validate=True)
                return hmac.compare_digest(actual, f"{self.username}:{self.password}".encode())
            except ValueError:
                return False
        if not value.startswith("Digest "):
            return False
        fields = dict((m[0], m[1] or m[2]) for m in re.findall(r'(\w+)=(?:"([^"\r\n]*)"|([^,\s]+))', value[7:]))
        try:
            if (
                fields["username"] != self.username
                or fields["realm"] != REALM
                or fields.get("algorithm", "MD5").upper() != "MD5"
                or fields["qop"] != "auth"
                or fields["uri"] != request.raw_path
                or not self.valid_nonce(fields["nonce"])
            ):
                return False

            def md5(s):
                return hashlib.md5(s.encode()).hexdigest()

            a1 = md5(f"{self.username}:{REALM}:{self.password}")
            a2 = md5(f"{request.method}:{fields['uri']}")
            expected = md5(f"{a1}:{fields['nonce']}:{fields['nc']}:{fields['cnonce']}:auth:{a2}")
            return hmac.compare_digest(fields["response"], expected) and self.claim(
                "http:" + fields["nonce"] + fields["cnonce"], self.clock() + 300, int(fields["nc"], 16)
            )
        except (KeyError, ValueError):
            return False

    def challenge(self):
        return f'Digest realm="{REALM}", nonce="{self.nonce()}", algorithm=MD5, qop="auth"'


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
