from __future__ import annotations

import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mars_ground_station import GroundStationService


class GroundHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], frontend_dir: Path, service: GroundStationService) -> None:
        super().__init__(address, GroundRequestHandler)
        self.frontend_dir = frontend_dir.resolve()
        self.service = service


class GroundRequestHandler(BaseHTTPRequestHandler):
    server: GroundHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ground] {self.client_address[0]} {fmt % args}")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        return payload

    def _static(self, relative: str) -> None:
        target = (self.server.frontend_dir / relative).resolve()
        try:
            target.relative_to(self.server.frontend_dir)
        except ValueError:
            self.send_error(404)
            return
        if not target.is_file():
            target = self.server.frontend_dir / "index.html"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/health":
            self._json({"ready": True, "team_id": self.server.service.team_id, "mode": "REAL_ROVER_HITL"})
            return
        if path == "/api/status":
            self._json(self.server.service.snapshot())
            return
        if path.startswith("/api/map/"):
            rover_id = urllib.parse.unquote(path.removeprefix("/api/map/"))
            row = self.server.service.map_for(rover_id)
            self._json(row if row is not None else {"error": "map unavailable"}, 200 if row is not None else 404)
            return
        if path.startswith("/api/camera/"):
            rover_id = urllib.parse.unquote(path.removeprefix("/api/camera/"))
            row = self.server.service.camera_frame(rover_id)
            if row is None:
                self.send_error(404, "camera unavailable")
                return
            frame, received_at = row
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("X-Frame-Received-At", str(received_at))
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
            return
        if path == "/api/commands/pending":
            team_id = str(query.get("team_id", [""])[0])
            if team_id != self.server.service.team_id:
                self._json({"error": "team_id mismatch"}, 403)
                return
            self._json({"commands": self.server.service.pop_pending_commands()})
            return
        if path == "/api/stream":
            try:
                after = int(query.get("after", ["-1"])[0])
            except ValueError:
                after = -1
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _ in range(30):
                    snapshot = self.server.service.wait_for_revision(after, timeout=1.0)
                    revision = int(snapshot.get("revision", 0))
                    data = json.dumps(snapshot, ensure_ascii=False, default=str)
                    self.wfile.write(f"id: {revision}\nevent: snapshot\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    after = revision
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if path == "/" or path == "":
            self._static("index.html")
            return
        self._static(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if parsed.path == "/api/camera/frame":
                query = urllib.parse.parse_qs(parsed.query)
                team_id = str(query.get("team_id", [""])[0])
                rover_id = str(query.get("rover_id", [""])[0])
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 2_500_000:
                    raise ValueError("invalid camera frame size")
                self.server.service.ingest_camera_frame(team_id, rover_id, self.rfile.read(length))
                self._json({"ok": True})
                return
            body = self._read_json()
            routes = {
                "/api/telemetry": self.server.service.ingest_telemetry,
                "/api/discovery": self.server.service.ingest_discovery,
                "/api/event": self.server.service.ingest_event,
                "/api/map": self.server.service.ingest_map,
                "/api/path": self.server.service.ingest_path,
                "/api/operator": self.server.service.queue_operator_command,
            }
            if parsed.path in routes:
                self._json({"ok": True, "result": routes[parsed.path](body)})
                return
            self._json({"error": "unknown endpoint"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
