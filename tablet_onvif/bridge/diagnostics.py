"""Opt-in diagnostics with a single redaction boundary for application and child logs."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit


class Redactor:
    def __init__(self):
        self.secrets = set()

    def add(self, *values):
        for value in values:
            if isinstance(value, str) and value:
                self.secrets.update((value, quote(value, safe="")))

    def credentials(self, username, password):
        self.add(password)
        if username and password:
            self.add(base64.b64encode(f"{username}:{password}".encode()).decode())

    def clean(self, message):
        text = str(message)
        text = re.sub(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+", r"\1[redacted]", text)
        # Keep endpoint identity useful, but remove URL credentials, paths and query tokens.
        text = re.sub(
            r"(?i)\b(?:https?|wss?|rtsps?|tcp|udp)://[^\s<>\"\']+", lambda match: endpoint(match.group(0)), text
        )
        for secret in sorted(self.secrets, key=len, reverse=True):
            if len(secret) >= 4:
                text = text.replace(secret, "[redacted]")
            else:
                text = re.sub(r"(?<!\w)" + re.escape(secret) + r"(?!\w)", "[redacted]", text)
        return text


def endpoint(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "[invalid-host]"
        if ":" in host:
            host = "[" + host + "]"
        port = ":" + str(parsed.port) if parsed.port else ""
        path = "/[redacted-path]" if parsed.path and parsed.path != "/" else parsed.path
        return f"{parsed.scheme}://{host}{port}{path}"
    except ValueError:
        return "[redacted-url]"


REDACTOR = Redactor()


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return REDACTOR.clean(super().format(record))


def debug_option(options_path=None):
    """Supervisor configuration takes precedence; Docker can use DEBUG_LOGGING."""
    path = Path(options_path or os.environ.get("OPTIONS_PATH", "/data/options.json"))
    if path.exists():
        try:
            value = json.loads(path.read_text()).get("debug_logging", False)
            return value is True
        except (OSError, ValueError, AttributeError):
            logging.getLogger(__name__).warning("Could not read debug_logging option; debug logging is off")
            return False
    return os.environ.get("DEBUG_LOGGING", "").lower() in ("true", "1", "yes", "on")


def configure_logging(debug):
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("bridge").setLevel(logging.DEBUG if debug else logging.INFO)
    for name in ("aiohttp.access", "aiohttp.client", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("HA_TOKEN", "SUPERVISOR_TOKEN"):
        REDACTOR.add(os.environ.get(name, ""))
    logging.getLogger(__name__).info("Debug logging %s", "enabled" if debug else "disabled")


def register_camera(camera):
    REDACTOR.credentials(camera.username, camera.password)
    REDACTOR.credentials(camera.source_username, camera.source_password)


async def drain_output(stream, logger, label):
    if stream is None:
        return
    # Bounded chunks prevent child output with long/no newlines from stalling its pipe.
    pending = b""
    discarding = False
    count = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            if pending and not discarding:
                logger.debug("%s: %s", label, REDACTOR.clean(pending.decode(errors="replace")))
            return
        if discarding:
            if b"\n" not in chunk:
                continue
            chunk = chunk.split(b"\n", 1)[1]
            discarding = False
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            count += 1
            if len(line) > 8192:
                logger.debug("%s: oversized child log record omitted", label)
            elif count <= 2000:
                logger.debug("%s: %s", label, REDACTOR.clean(line.decode(errors="replace")))
            elif count == 2001:
                logger.debug("%s: further output suppressed for this process", label)
        if len(pending) > 8192:
            pending = b""
            discarding = True
            logger.debug("%s: oversized child log record omitted", label)


async def finish_output(task):
    if task:
        try:
            await asyncio.wait_for(task, 2)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
