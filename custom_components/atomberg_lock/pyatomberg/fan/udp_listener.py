"""Async UDP broadcast listener for real-time local Atomberg fan state updates."""

import asyncio
import json
import logging
import socket
from typing import Any, Callable

from pyatomberg.const import FAN_UDP_MAGIC_PREFIX, FAN_UDP_PORT

_LOGGER = logging.getLogger(__name__)


def parse_fan_datagram(data: bytes) -> tuple[str, dict[str, Any]] | None:
    """Parse raw UDP broadcast datagram from an Atomberg ceiling fan.

    Expected format:
      PROXY TCP4 <src_ip> <dst_ip> <src_port> <dst_port>\\r\\n
      {"device_id":"ab_fan_xxx","power":1,"speed":4,"sleep":0,"timer":0,...}
    or direct JSON payload.

    Returns (device_id, payload_dict) or None if invalid.
    """
    try:
        text = data.decode("utf-8", errors="ignore").strip()
        json_start = text.find("{")
        json_end = text.rfind("}")

        if json_start == -1 or json_end == -1 or json_start >= json_end:
            return None

        payload_str = text[json_start : json_end + 1]
        payload = json.loads(payload_str)

        device_id = payload.get("device_id") or payload.get("id")
        if not device_id:
            return None

        return str(device_id), payload
    except Exception as err:
        _LOGGER.debug("Failed to parse fan UDP datagram: %s", err)
        return None


class AtombergUDPProtocol(asyncio.DatagramProtocol):
    """Asyncio datagram protocol for receiving fan broadcasts."""

    def __init__(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Initialize protocol with callback."""
        self._callback = callback
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handle connection established."""
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming datagram."""
        parsed = parse_fan_datagram(data)
        if parsed:
            device_id, payload = parsed
            try:
                self._callback(device_id, payload)
            except Exception as err:
                _LOGGER.exception("Error in fan UDP callback: %s", err)

    def error_received(self, exc: Exception) -> None:
        """Handle datagram error."""
        _LOGGER.debug("UDP listener received error: %s", exc)


class AtombergUDPListener:
    """Manages the background UDP socket listener on port 5625."""

    def __init__(
        self,
        port: int = FAN_UDP_PORT,
        callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize UDP listener."""
        self._port = port
        self._callbacks: list[Callable[[str, dict[str, Any]], None]] = []
        if callback:
            self._callbacks.append(callback)
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: AtombergUDPProtocol | None = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return True if listener is active."""
        return self._is_running

    def register_callback(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Register a callback for incoming state updates."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Unregister an existing callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _dispatch_update(self, device_id: str, payload: dict[str, Any]) -> None:
        """Dispatch update to all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb(device_id, payload)
            except Exception as err:
                _LOGGER.exception("Error executing fan update callback: %s", err)

    async def start(self) -> None:
        """Start listening for broadcast packets on port 5625."""
        if self._is_running:
            return

        loop = asyncio.get_running_loop()

        # Create socket configured for UDP broadcast
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_BROADCAST"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)

        try:
            sock.bind(("", self._port))
        except OSError as err:
            sock.close()
            _LOGGER.warning("Could not bind UDP listener to port %d: %s", self._port, err)
            raise

        transport, protocol = await loop.create_datagram_endpoint(
            lambda: AtombergUDPProtocol(self._dispatch_update),
            sock=sock,
        )
        self._transport = transport
        self._protocol = protocol
        self._is_running = True
        _LOGGER.debug("Atomberg UDP listener active on port %d", self._port)

    async def stop(self) -> None:
        """Stop UDP listener and close transport."""
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None
        self._is_running = False
        _LOGGER.debug("Atomberg UDP listener stopped")
