"""SSE events endpoint for real-time updates."""

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["events"])


@router.get("/events")
async def event_stream(request: Request):
    """SSE endpoint for real-time event streaming."""
    event_bus = request.app.state.event_bus
    queue = event_bus.subscribe()

    async def generate():
        try:
            # Send an immediate connected event so the browser knows the stream is alive
            yield {
                "event": "connected",
                "data": json.dumps({"type": "connected", "status": "ok"}),
            }
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "event": event["type"],
                        "data": json.dumps(event),
                    }
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(generate(), ping=20)
