"""SSE Event Bus - bridges synchronous orchestrator events to async SSE consumers."""

import asyncio
import json
import threading
from datetime import datetime
from typing import Dict, Any, List


class EventBus:
    """Thread-safe event bus for SSE streaming.

    The orchestrator runs in background threads and publishes events synchronously.
    SSE consumers read events asynchronously via asyncio queues.
    """

    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the asyncio event loop for thread-safe publishing."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscription queue for SSE consumer."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        q = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove a subscription queue."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_type: str, data: Dict[str, Any]):
        """Publish an event to all subscribers. Thread-safe.

        Args:
            event_type: Event type string (e.g. 'phase_change', 'log')
            data: Event data dictionary
        """
        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }

        with self._lock:
            for q in self._subscribers:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._safe_put, q, event)
                else:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

    def _safe_put(self, q: asyncio.Queue, event: dict):
        """Safely put an event into a queue."""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
