from __future__ import annotations

import asyncio
import socket
import struct
import time
import uuid
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring

D = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
A = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
S = "http://www.w3.org/2003/05/soap-envelope"
NETWORK = "http://www.onvif.org/ver10/network/wsdl"


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, cameras):
        self.cameras = cameras
        self.transport = None
        self.buckets = {}
        self.sequence = 0
        self.instance = int(time.time())

    @classmethod
    async def start(cls, cameras, interface):
        protocol = cls(cameras)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 3702))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton("239.255.255.250") + socket.inet_aton(interface),
        )
        if interface != "0.0.0.0":
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 1))
        sock.setblocking(False)
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: protocol, sock=sock)
        for c in cameras:
            protocol.send("Hello", [c], ("239.255.255.250", 3702))
        return protocol

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) > 16384:
            return
        now = time.monotonic()
        self.buckets = {k: v for k, v in self.buckets.items() if v[0] > now - 10}
        previous, count = self.buckets.get(addr[0], (now, 0))
        if count >= 20 or len(self.buckets) > 1024:
            return
        self.buckets[addr[0]] = (previous, count + 1)
        try:
            root = fromstring(data)
            body = root.find(f"{{{S}}}Body")
            if body is None or not len(body):
                return
            operation = body[0]
            name = operation.tag.rsplit("}", 1)[-1]
            relates = root.findtext(f"{{{S}}}Header/{{{A}}}MessageID", "")
            cameras = self.cameras
            if name == "Probe":
                types = operation.findtext(f"{{{D}}}Types", "")
                if types and "NetworkVideoTransmitter" not in types:
                    return
                scopes = operation.findtext(f"{{{D}}}Scopes", "").split()
                cameras = [c for c in cameras if all(s in self.scopes(c).split() for s in scopes)]
                if cameras:
                    self.send("ProbeMatches", cameras, addr, relates)
            elif name == "Resolve":
                target = operation.findtext(f"{{{A}}}EndpointReference/{{{A}}}Address", "")
                cameras = [c for c in cameras if target == "urn:uuid:" + c.identity]
                if cameras:
                    self.send("ResolveMatches", cameras, addr, relates)
        except Exception:
            return

    def scopes(self, c):
        return " ".join(
            (
                "onvif://www.onvif.org/type/video_encoder",
                "onvif://www.onvif.org/Profile/Streaming",
                "onvif://www.onvif.org/name/" + c.camera.id,
                "onvif://www.onvif.org/hardware/tablet_onvif",
            )
        )

    def send(self, kind, cameras, addr, relates=""):
        envelope = ET.Element(f"{{{S}}}Envelope", {"xmlns:dn": NETWORK})
        header = ET.SubElement(envelope, f"{{{S}}}Header")
        for name, value in (
            ("MessageID", "urn:uuid:" + str(uuid.uuid4())),
            ("Action", D + "/" + kind),
            (
                "To",
                "urn:schemas-xmlsoap-org:ws:2005:04:discovery" if kind in ("Hello", "Bye") else A + "/role/anonymous",
            ),
        ):
            ET.SubElement(header, f"{{{A}}}{name}").text = value
        if relates:
            ET.SubElement(header, f"{{{A}}}RelatesTo").text = relates
        self.sequence += 1
        ET.SubElement(header, f"{{{D}}}AppSequence", InstanceId=str(self.instance), MessageNumber=str(self.sequence))
        body = ET.SubElement(envelope, f"{{{S}}}Body")
        output = ET.SubElement(body, f"{{{D}}}{kind}")
        for c in cameras:
            match = (
                ET.SubElement(output, f"{{{D}}}{'ProbeMatch' if kind == 'ProbeMatches' else 'ResolveMatch'}")
                if kind.endswith("Matches")
                else output
            )
            ref = ET.SubElement(match, f"{{{A}}}EndpointReference")
            ET.SubElement(ref, f"{{{A}}}Address").text = "urn:uuid:" + c.identity
            if kind != "Bye":
                ET.SubElement(match, f"{{{D}}}Types").text = "dn:NetworkVideoTransmitter"
                ET.SubElement(match, f"{{{D}}}Scopes").text = self.scopes(c)
                ET.SubElement(match, f"{{{D}}}XAddrs").text = c.base + "/onvif/device_service"
                ET.SubElement(match, f"{{{D}}}MetadataVersion").text = "1"
        self.transport.sendto(ET.tostring(envelope, encoding="utf-8", xml_declaration=True), addr)

    def close(self):
        if self.transport:
            for c in self.cameras:
                self.send("Bye", [c], ("239.255.255.250", 3702))
            self.transport.close()
