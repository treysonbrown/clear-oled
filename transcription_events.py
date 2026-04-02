#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json


def format_sse_event(event_type, data):
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


class EventBroadcaster:
    def __init__(self):
        self.subscribers = set()
        self.lock = asyncio.Lock()

    async def publish(self, event_type, data):
        message = format_sse_event(event_type, data)
        async with self.lock:
            subscribers = list(self.subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def subscribe(self):
        queue = asyncio.Queue(maxsize=100)
        async with self.lock:
            self.subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue):
        async with self.lock:
            self.subscribers.discard(queue)
