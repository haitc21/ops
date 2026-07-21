"""Shared helpers for messaging integration tests."""

from __future__ import annotations

import asyncio
import json

import aio_pika


def command_body(marker: str) -> bytes:
    return json.dumps({"message_type": "command", "marker": marker}).encode()


def body_marker(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        marker = payload.get("marker")
        if isinstance(marker, str):
            return marker
    return None


async def get_queue_message_by_marker(
    queue: aio_pika.abc.AbstractQueue,
    marker: str,
    *,
    deadline: float,
) -> aio_pika.abc.AbstractIncomingMessage | None:
    while asyncio.get_running_loop().time() < deadline:
        message = await queue.get(timeout=0.2, fail=False)
        if message is None:
            continue
        if body_marker(message.body) == marker:
            return message
        await message.reject(requeue=False)
    return None
