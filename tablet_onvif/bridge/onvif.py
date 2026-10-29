"""Focused ONVIF Device/Media/PullPoint services, implemented independently."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from aiohttp import web
from defusedxml.ElementTree import fromstring

from .auth import Authentication, utc
from .events import Events

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsa": "http://www.w3.org/2005/08/addressing",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wstop": "http://docs.oasis-open.org/wsn/t-1",
    "tns1": "http://www.onvif.org/ver10/topics",
    "ter": "http://www.onvif.org/ver10/error",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def node(parent, name, text=None, **attrs):
    prefix, local = name.split(":")
    value = ET.SubElement(parent, f"{{{NS[prefix]}}}{local}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        value.text = str(text)
    return value


def duration(value, default=60, maximum=3600):
    if not value:
        return default
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if match and any(match.groups()):
        h, m, s = (float(x or 0) for x in match.groups())
        return min(max(h * 3600 + m * 60 + s, 0), maximum)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return min(max((stamp - datetime.now(timezone.utc)).total_seconds(), 0), maximum)
    except ValueError:
        raise ValueError("Invalid duration") from None


class Fault(Exception):
    def __init__(self, reason, code="ter:InvalidArgVal"):
        self.reason, self.code = reason, code


class Onvif:
    def __init__(self, camera, identity, video):
        self.camera, self.identity, self.video = camera, identity, video
        self.auth = Authentication(camera.username, camera.password)
        self.events = Events()
        self.runner = None

    @property
    def base(self):
        return f"http://{self.camera.advertise_host}:{self.camera.onvif_port}"

    @property
    def mac(self):
        digest = hashlib.sha256(self.identity.encode()).digest()
        return ":".join(f"{x:02x}" for x in [2, *digest[:5]])

    async def start(self):
        app = web.Application(client_max_size=65536)
        app.router.add_post("/onvif/{tail:.*}", self.soap)
        app.router.add_get("/snapshot", self.snapshot)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", self.camera.onvif_port).start()

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self.runner = None

    async def snapshot(self, request):
        if not self.auth.http(request):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": self.auth.challenge()})
        if not self.video.jpeg or not self.video.status()["video_available"]:
            raise web.HTTPServiceUnavailable(text="Tablet camera unavailable")
        return web.Response(body=self.video.jpeg, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def soap(self, request):
        root = None
        try:
            root = fromstring(await request.read())
            body = root.find("s:Body", NS)
            if body is None or len(body) != 1:
                raise Fault("SOAP body must contain one operation")
            op = body[0]
            name = op.tag.rsplit("}", 1)[-1]
            if name != "GetSystemDateAndTime" and not (self.auth.http(request) or self.auth.wsse(root)):
                return self.response(root, None, Fault("Authentication required", "ter:NotAuthorized"), 401)
            response = await self.dispatch(name, op, request.path)
            return self.response(root, response)
        except Fault as exc:
            return self.response(root, None, exc, 400)
        except (ValueError, KeyError):
            return self.response(root, None, Fault("Invalid request or expired subscription"), 400)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Avoid reflecting input XML, tokens, or endpoint secrets into errors/logs.
            return self.response(root, None, Fault("Malformed or unsupported SOAP request"), 400)

    def response(self, request, payload, fault=None, status=200):
        envelope = ET.Element(f"{{{NS['s']}}}Envelope", {"xmlns:ter": NS["ter"]})
        header = node(envelope, "s:Header")
        if request is not None:
            action = request.findtext("wsa:Header/wsa:Action", namespaces=NS)
            # SOAP Header is in the envelope namespace.
            action = request.findtext("s:Header/wsa:Action", default=action or "", namespaces=NS)
            message_id = request.findtext("s:Header/wsa:MessageID", namespaces=NS)
            if action:
                node(
                    header,
                    "wsa:Action",
                    "http://www.w3.org/2005/08/addressing/soap/fault"
                    if fault
                    else (action[:-7] if action.endswith("Request") else action) + "Response",
                )
            if message_id:
                node(header, "wsa:RelatesTo", message_id)
        node(header, "wsa:MessageID", "urn:uuid:" + str(uuid.uuid4()))
        if payload is None or not any(e.tag.startswith("{" + NS["tns1"] + "}") for e in payload.iter()):
            envelope.set("xmlns:tns1", NS["tns1"])
        body = node(envelope, "s:Body")
        if fault:
            f = node(body, "s:Fault")
            code = node(f, "s:Code")
            node(code, "s:Value", "s:Sender")
            node(node(code, "s:Subcode"), "s:Value", fault.code)
            reason = node(f, "s:Reason")
            text = node(reason, "s:Text", fault.reason)
            text.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
        else:
            body.append(payload)
        headers = {"Cache-Control": "no-store"}
        if status == 401:
            headers["WWW-Authenticate"] = self.auth.challenge()
        return web.Response(
            body=ET.tostring(envelope, encoding="utf-8", xml_declaration=True),
            status=status,
            content_type="application/soap+xml",
            headers=headers,
        )

    def encoder(self, parent, profile, tag="trt:Configurations"):
        c = self.camera
        low = profile == "sub"
        value = node(parent, tag, token=profile)
        node(value, "tt:Name", profile)
        node(value, "tt:UseCount", 1)
        node(value, "tt:Encoding", "H264")
        resolution = node(value, "tt:Resolution")
        node(resolution, "tt:Width", max(160, c.width // 4 * 2) if low else c.width)
        node(resolution, "tt:Height", max(120, c.height // 4 * 2) if low else c.height)
        node(value, "tt:Quality", 5)
        rate = node(value, "tt:RateControl")
        node(rate, "tt:FrameRateLimit", c.fps)
        node(rate, "tt:EncodingInterval", 1)
        node(rate, "tt:BitrateLimit", max(150, c.bitrate // 4) if low else c.bitrate)
        h264 = node(value, "tt:H264")
        node(h264, "tt:GovLength", c.fps * 2)
        node(h264, "tt:H264Profile", "Main")
        multi = node(value, "tt:Multicast")
        addr = node(multi, "tt:Address")
        node(addr, "tt:Type", "IPv4")
        node(addr, "tt:IPv4Address", "0.0.0.0")
        node(multi, "tt:Port", 0)
        node(multi, "tt:TTL", 1)
        node(multi, "tt:AutoStart", "false")
        node(value, "tt:SessionTimeout", "PT60S")
        return value

    def source_config(self, parent, tag="trt:Configurations"):
        c = self.camera
        value = node(parent, tag, token="source")
        node(value, "tt:Name", c.name)
        node(value, "tt:UseCount", 2)
        node(value, "tt:SourceToken", "source")
        node(value, "tt:Bounds", x=0, y=0, width=c.width, height=c.height)
        return value

    def profile(self, response, profile, singular=False):
        p = node(response, "trt:Profile" if singular else "trt:Profiles", token=profile, fixed="true")
        node(p, "tt:Name", "High quality" if profile == "main" else "Low quality")
        self.source_config(p, "tt:VideoSourceConfiguration")
        self.encoder(p, profile, "tt:VideoEncoderConfiguration")

    async def dispatch(self, name, op, path):
        def response(prefix):
            return ET.Element(f"{{{NS[prefix]}}}{name}Response")

        def profile_token():
            p = (
                op.findtext("trt:ProfileToken", namespaces=NS)
                or op.findtext("trt:ConfigurationToken", namespaces=NS)
                or "main"
            )
            if p not in ("main", "sub"):
                raise Fault("Unknown video profile")
            return p

        c = self.camera
        if name == "GetSystemDateAndTime":
            r = response("tds")
            d = node(r, "tds:SystemDateAndTime")
            node(d, "tt:DateTimeType", "NTP")
            node(d, "tt:DaylightSavings", "false")
            node(node(d, "tt:TimeZone"), "tt:TZ", "UTC0")
            now = datetime.now(timezone.utc)
            dt = node(d, "tt:UTCDateTime")
            t = node(dt, "tt:Time")
            for tag, value in (("Hour", now.hour), ("Minute", now.minute), ("Second", now.second)):
                node(t, "tt:" + tag, value)
            date = node(dt, "tt:Date")
            for tag, value in (("Year", now.year), ("Month", now.month), ("Day", now.day)):
                node(date, "tt:" + tag, value)
            return r
        if name == "GetDeviceInformation":
            r = response("tds")
            for key, value in {
                "Manufacturer": "Tablet ONVIF",
                "Model": "Companion Camera",
                "FirmwareVersion": "0.1.1",
                "SerialNumber": self.identity,
                "HardwareId": self.identity,
            }.items():
                node(r, "tds:" + key, value)
            return r
        if name == "GetCapabilities":
            r = response("tds")
            caps = node(r, "tds:Capabilities")
            for service, endpoint in (
                ("Device", "device_service"),
                ("Media", "media_service"),
                ("Events", "events_service"),
            ):
                part = node(caps, "tt:" + service)
                node(part, "tt:XAddr", self.base + "/onvif/" + endpoint)
                if service == "Media":
                    stream = node(part, "tt:StreamingCapabilities")
                    node(stream, "tt:RTPMulticast", "false")
                    node(stream, "tt:RTP_TCP", "true")
                    node(stream, "tt:RTP_RTSP_TCP", "true")
                if service == "Events":
                    node(part, "tt:WSSubscriptionPolicySupport", "false")
                    node(part, "tt:WSPullPointSupport", "true")
                    node(part, "tt:WSPausableSubscriptionManagerInterfaceSupport", "false")
            return r
        if name == "GetServices":
            r = response("tds")
            for prefix, endpoint in (("tds", "device_service"), ("trt", "media_service"), ("tev", "events_service")):
                service = node(r, "tds:Service")
                node(service, "tds:Namespace", NS[prefix])
                node(service, "tds:XAddr", self.base + "/onvif/" + endpoint)
                version = node(service, "tds:Version")
                node(version, "tt:Major", 2)
                node(version, "tt:Minor", 0)
            return r
        if name == "GetScopes":
            r = response("tds")
            for value in (
                "onvif://www.onvif.org/type/video_encoder",
                "onvif://www.onvif.org/Profile/Streaming",
                "onvif://www.onvif.org/name/" + c.id,
                "onvif://www.onvif.org/hardware/tablet_onvif",
            ):
                scope = node(r, "tds:Scopes")
                node(scope, "tt:ScopeDef", "Fixed")
                node(scope, "tt:ScopeItem", value)
            return r
        if name == "GetNetworkInterfaces":
            r = response("tds")
            iface = node(r, "tds:NetworkInterfaces", token=c.id)
            node(iface, "tt:Enabled", "true")
            info = node(iface, "tt:Info")
            node(info, "tt:Name", c.id)
            node(info, "tt:HwAddress", self.mac)
            ipv4 = node(iface, "tt:IPv4")
            node(ipv4, "tt:Enabled", "true")
            cfg = node(ipv4, "tt:Config")
            node(node(cfg, "tt:Manual"), "tt:Address", c.advertise_host)
            node(cfg.find("tt:Manual", NS), "tt:PrefixLength", 24)
            node(cfg, "tt:DHCP", "false")
            return r
        if name in ("GetProfiles", "GetProfile"):
            r = response("trt")
            for p in (profile_token(),) if name == "GetProfile" else ("main", "sub"):
                self.profile(r, p, name == "GetProfile")
            return r
        if name in (
            "GetVideoEncoderConfigurations",
            "GetVideoEncoderConfiguration",
            "GetCompatibleVideoEncoderConfigurations",
        ):
            r = response("trt")
            for p in (profile_token(),) if name == "GetVideoEncoderConfiguration" else ("main", "sub"):
                self.encoder(
                    r, p, "trt:Configuration" if name == "GetVideoEncoderConfiguration" else "trt:Configurations"
                )
            return r
        if name in (
            "GetVideoSourceConfigurations",
            "GetVideoSourceConfiguration",
            "GetCompatibleVideoSourceConfigurations",
        ):
            r = response("trt")
            self.source_config(
                r, "trt:Configuration" if name == "GetVideoSourceConfiguration" else "trt:Configurations"
            )
            return r
        if name == "GetVideoSources":
            r = response("trt")
            src = node(r, "trt:VideoSources", token="source")
            node(src, "tt:Framerate", c.fps)
            res = node(src, "tt:Resolution")
            node(res, "tt:Width", c.width)
            node(res, "tt:Height", c.height)
            return r
        if name in ("GetStreamUri", "GetSnapshotUri"):
            r = response("trt")
            p = profile_token()
            protocol = op.findtext("trt:StreamSetup/tt:Transport/tt:Protocol", namespaces=NS)
            if name == "GetStreamUri" and protocol not in (None, "TCP", "RTSP"):
                raise Fault("Only RTSP over TCP is supported", "ter:InvalidStreamSetup")
            media = node(r, "trt:MediaUri")
            node(
                media,
                "tt:Uri",
                self.video.rtsp(p, external=True) if name == "GetStreamUri" else self.base + "/snapshot",
            )
            node(media, "tt:InvalidAfterConnect", "false")
            node(media, "tt:InvalidAfterReboot", "false")
            node(media, "tt:Timeout", "PT60S")
            return r
        if name == "GetVideoEncoderConfigurationOptions":
            r = response("trt")
            opts = node(r, "trt:Options")
            q = node(opts, "tt:QualityRange")
            node(q, "tt:Min", 1)
            node(q, "tt:Max", 10)
            enc = node(opts, "tt:H264")
            for width, height in ((c.width, c.height), (max(160, c.width // 4 * 2), max(120, c.height // 4 * 2))):
                res = node(enc, "tt:ResolutionsAvailable")
                node(res, "tt:Width", width)
                node(res, "tt:Height", height)
            for tag, maximum in (
                ("GovLengthRange", c.fps * 2),
                ("FrameRateRange", c.fps),
                ("EncodingIntervalRange", 1),
            ):
                ran = node(enc, "tt:" + tag)
                node(ran, "tt:Min", 1)
                node(ran, "tt:Max", maximum)
            node(enc, "tt:H264ProfilesSupported", "Main")
            return r
        if name == "GetEventProperties":
            r = response("tev")
            node(r, "tev:TopicNamespaceLocation", "http://www.onvif.org/onvif/ver10/topics/topicns.xml")
            node(r, "tev:FixedTopicSet", "true")
            topics = node(r, "wstop:TopicSet")
            rule = ET.SubElement(topics, f"{{{NS['tns1']}}}RuleEngine")
            cell = ET.SubElement(rule, "CellMotionDetector")
            motion = ET.SubElement(cell, "Motion", {f"{{{NS['wstop']}}}topic": "true"})
            desc = node(motion, "tt:MessageDescription", IsProperty="true")
            source = node(desc, "tt:Source")
            node(source, "tt:SimpleItemDescription", Name="VideoSourceConfigurationToken", Type="tt:ReferenceToken")
            node(source, "tt:SimpleItemDescription", Name="Rule", Type="xsd:string")
            node(node(desc, "tt:Data"), "tt:SimpleItemDescription", Name="IsMotion", Type="xsd:boolean")
            r.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
            node(r, "tev:TopicExpressionDialect", "http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet")
            node(r, "tev:MessageContentFilterDialect", "http://www.onvif.org/ver10/tev/messageContentFilter/ItemFilter")
            node(r, "tev:MessageContentSchemaLocation", "http://www.onvif.org/onvif/ver10/schema/onvif.xsd")
            return r
        if name == "CreatePullPointSubscription":
            for expression in op.findall(".//wsnt:TopicExpression", NS):
                if expression.text and not any(
                    x in expression.text for x in ("Motion", "RuleEngine", "CellMotionDetector")
                ):
                    raise Fault("Only motion topics are supported")
            ttl = duration(op.findtext("tev:InitialTerminationTime", namespaces=NS))
            key = self.events.create(ttl)
            r = response("tev")
            ref = node(r, "tev:SubscriptionReference")
            node(ref, "wsa:Address", self.base + "/onvif/subscriptions/" + key)
            self.subscription_times(r, key, "wsnt")
            return r
        if name in ("PullMessages", "Renew", "Unsubscribe", "SetSynchronizationPoint"):
            key = path.rsplit("/", 1)[-1]
            sub = self.events.get(key)
            if name == "Unsubscribe":
                self.events.subscriptions.pop(key).ready.set()
                return response("wsnt")
            if name == "Renew":
                ttl = duration(op.findtext("wsnt:TerminationTime", namespaces=NS))
                sub.expires = self.events.clock() + max(ttl, 1)
                r = response("wsnt")
                node(r, "wsnt:TerminationTime", self.expiration(key))
                node(r, "wsnt:CurrentTime", utc())
                return r
            r = response("tev")
            if name == "SetSynchronizationPoint":
                self.events.synchronize(key)
                return r
            timeout = duration(op.findtext("tev:Timeout", namespaces=NS), 5, 30)
            limit = int(op.findtext("tev:MessageLimit", default="32", namespaces=NS))
            messages = await self.events.pull(key, timeout, limit)
            self.subscription_times(r, key, "tev")
            for stamp, motion, operation in messages:
                notification = node(r, "wsnt:NotificationMessage")
                node(
                    notification,
                    "wsnt:Topic",
                    "tns1:RuleEngine/CellMotionDetector/Motion",
                    Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet",
                )
                message = node(
                    node(notification, "wsnt:Message"), "tt:Message", UtcTime=stamp, PropertyOperation=operation
                )
                source = node(message, "tt:Source")
                node(source, "tt:SimpleItem", Name="VideoSourceConfigurationToken", Value="source")
                node(source, "tt:SimpleItem", Name="Rule", Value="MotionRule")
                node(node(message, "tt:Data"), "tt:SimpleItem", Name="IsMotion", Value=str(motion).lower())
            return r
        if name == "GetServiceCapabilities":
            prefix = "tev" if "events" in path else "trt" if "media" in path else "tds"
            r = response(prefix)
            node(
                r,
                prefix + ":Capabilities",
                **({"WSPullPointSupport": "true", "MaxPullPoints": 64} if prefix == "tev" else {}),
            )
            return r
        raise Fault("Operation not implemented", "ter:ActionNotSupported")

    def expiration(self, key):
        sub = self.events.get(key)
        return (
            (datetime.now(timezone.utc) + timedelta(seconds=max(0, sub.expires - self.events.clock())))
            .isoformat()
            .replace("+00:00", "Z")
        )

    def subscription_times(self, response, key, prefix):
        node(response, prefix + ":CurrentTime", utc())
        node(response, prefix + ":TerminationTime", self.expiration(key))
