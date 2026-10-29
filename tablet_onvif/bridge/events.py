from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from .auth import utc


@dataclass
class Subscription:
    expires: float
    messages: deque = field(default_factory=lambda: deque(maxlen=128))
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class Events:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.motion = False
        self.available = False
        self.subscriptions = {}
        self.transitions = 0

    def update(self, state):
        self.available = state in ("on", "off")
        motion = state == "on"
        if motion != self.motion:
            self.motion = motion
            self.transitions += 1
            for sub in self.subscriptions.values():
                sub.messages.append((utc(), motion, "Changed"))
                sub.ready.set()

    def create(self, ttl=60):
        self.expire()
        if len(self.subscriptions) >= 64:
            raise ValueError("Subscription limit reached")
        key = secrets.token_urlsafe(24)
        sub = Subscription(self.clock() + min(max(ttl, 1), 3600))
        self.subscriptions[key] = sub
        self.synchronize(key)
        return key

    def expire(self):
        for key in list(self.subscriptions):
            if self.subscriptions[key].expires <= self.clock():
                self.subscriptions.pop(key).ready.set()

    def get(self, key):
        self.expire()
        if key not in self.subscriptions:
            raise KeyError("Subscription expired or not found")
        return self.subscriptions[key]

    def synchronize(self, key):
        sub = self.get(key)
        sub.messages.append((utc(), self.motion, "Initialized"))
        sub.ready.set()

    async def pull(self, key, timeout, limit):
        sub = self.get(key)
        if not sub.messages:
            sub.ready.clear()
            wait = min(max(timeout, 0), 30, max(0, sub.expires - self.clock()))
            try:
                await asyncio.wait_for(sub.ready.wait(), wait)
            except asyncio.TimeoutError:
                pass
        self.get(key)
        messages = []
        for _ in range(min(max(limit, 1), 128)):
            if not sub.messages:
                break
            messages.append(sub.messages.popleft())
        return messages
