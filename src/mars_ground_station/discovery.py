"""UDP listener for zero-configuration rover discovery on a trusted LAN."""
from __future__ import annotations

import json
import socket
import threading
from typing import Any


DISCOVERY_PROTOCOL = "hitl-rover-discovery-v1"
DEFAULT_DISCOVERY_PORT = 38765
DISCOVERY_MULTICAST_GROUP = "239.255.73.84"


class LanDiscoveryListener:
    def __init__(self, service: Any, port: int = DEFAULT_DISCOVERY_PORT) -> None:
        self.service = service
        self.port = int(port)
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", self.port))
        try:
            membership = socket.inet_aton(DISCOVERY_MULTICAST_GROUP) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        except OSError:
            # Broadcast and direct localhost discovery remain available on
            # hosts or restricted networks that disable multicast membership.
            pass
        self.port = int(sock.getsockname()[1])
        sock.settimeout(1.0)
        self._socket = sock
        self._thread = threading.Thread(target=self._run, name="rover-lan-discovery", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._socket = None

    def _run(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                raw, address = self._socket.recvfrom(16_384)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("protocol") != DISCOVERY_PROTOCOL:
                    continue
                self.service.ingest_network_announcement(payload, str(address[0]))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
