#!/usr/bin/env python3
"""Ground-computer ROS2/DDS gateway.

Runs on the same LAN / ROS_DOMAIN_ID as one rover team. It subscribes only to
that team's broadcast topics and mirrors them into the local web ground station.
Operator directives queued in the web station are republished onto the team's
DDS topic. No opponent topics or global-truth topics are consumed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=1.0) as response:  # noqa: S310 - operator configured local endpoint
        response.read()


def post_jpeg(url: str, frame: bytes) -> None:
    req = urllib.request.Request(url, data=frame, headers={"Content-Type": "image/jpeg"}, method="POST")
    with urllib.request.urlopen(req, timeout=2.0) as response:  # noqa: S310
        response.read()


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def ros_topic_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not segment:
        raise ValueError("team-id must contain a letter, digit, or underscore")
    return segment


def normalize_console_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("console_url must be an HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("console_url must not contain credentials, query, or fragment")
    normalized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    return normalized.rstrip("/")


class RoverLinkHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], gateway: Any) -> None:
        super().__init__(address, RoverLinkRequestHandler)
        self.gateway = gateway


class RoverLinkRequestHandler(BaseHTTPRequestHandler):
    server: RoverLinkHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        self._json({
            "ok": True,
            "team_id": self.server.gateway.team,
            "rovers": self.server.gateway.visible_rover_ids(),
            "console_url": self.server.gateway.current_console(),
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/connect":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 16_384:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            result = self.server.gateway.connect_console(
                str(payload.get("team_id") or ""),
                str(payload.get("rover_id") or ""),
                str(payload.get("console_url") or ""),
            )
            self._json({"ok": True, "result": result})
        except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS2 team broadcast <-> local MARS ground station")
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--console", default="http://127.0.0.1:8080")
    parser.add_argument("--command-poll-s", type=float, default=0.25)
    parser.add_argument("--discovery-port", type=int, default=38765)
    parser.add_argument("--announce-interval-s", type=float, default=2.0)
    parser.add_argument("--agent-host", default="0.0.0.0")
    parser.add_argument("--agent-port", type=int, default=38766)
    args = parser.parse_args()

    try:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import CompressedImage
        from std_msgs.msg import String
    except Exception as exc:
        raise SystemExit(f"ROS 2 Humble Python environment required: {exc}")

    base = normalize_console_url(args.console)
    team = args.team_id.strip()
    topic_team = ros_topic_segment(team)
    prefix = f"/team/{topic_team}"

    class Gateway(Node):
        def __init__(self) -> None:
            super().__init__(f"mars_ground_gateway_{topic_team}")
            self.lock = threading.Lock()
            self.team = team
            self.console_base = base
            self.create_subscription(String, f"{prefix}/fleet_state", self.on_state, 50)
            self.create_subscription(String, f"{prefix}/resource_discovery", self.on_discovery, 50)
            self.create_subscription(String, f"{prefix}/event", self.on_event, 50)
            self.create_subscription(String, f"{prefix}/map", self.on_map, 10)
            self.create_subscription(String, f"{prefix}/path", self.on_path, 20)
            self.create_subscription(CompressedImage, f"{prefix}/camera/compressed", self.on_camera, 10)
            self.command_pub = self.create_publisher(String, f"{prefix}/operator_directive", 50)
            self.outbox: dict[str, dict] = {}
            self.visible_rovers: dict[str, tuple[dict, float]] = {}
            self.link_server: RoverLinkHTTPServer | None = None
            self.link_thread: threading.Thread | None = None
            try:
                self.link_server = RoverLinkHTTPServer((args.agent_host, args.agent_port), self)
                self.link_thread = threading.Thread(
                    target=self.link_server.serve_forever,
                    name="rover-link-agent",
                    daemon=True,
                )
                self.link_thread.start()
            except OSError as exc:
                self.get_logger().warning(f"rover link agent unavailable: {exc}")
            self.discovery_stop = threading.Event()
            self.discovery_thread = threading.Thread(
                target=self.announce_rovers,
                name="rover-lan-announcer",
                daemon=True,
            )
            self.discovery_thread.start()
            self.create_timer(max(0.1, args.command_poll_s), self.pull_commands)
            self.get_logger().info(f"listening to {prefix} on current ROS_DOMAIN_ID")
            self.get_logger().info(f"announcing visible rovers on UDP {args.discovery_port}")
            if self.link_server is not None:
                self.get_logger().info(f"rover link agent listening on {args.agent_host}:{args.agent_port}")

        def current_console(self) -> str:
            with self.lock:
                return self.console_base

        def visible_rover_ids(self) -> list[str]:
            with self.lock:
                return sorted(self.visible_rovers)

        def connect_console(self, incoming_team: str, rover_id: str, console_url: str) -> dict[str, Any]:
            if incoming_team.strip() != team:
                raise ValueError("team_id mismatch")
            rover_id = rover_id.strip()
            with self.lock:
                if rover_id not in self.visible_rovers:
                    raise ValueError("rover is not currently visible to this gateway")
            normalized = normalize_console_url(console_url)
            health = get_json(normalized + "/api/health")
            if not health.get("ready") or str(health.get("team_id") or "") != team:
                raise ValueError("ground station health or team check failed")
            with self.lock:
                self.console_base = normalized
            self.get_logger().info(f"connected {rover_id} to ground station {normalized}")
            return {"rover_id": rover_id, "console_url": normalized}

        def _decode(self, msg: String) -> dict | None:
            try:
                payload = json.loads(msg.data)
                if not isinstance(payload, dict):
                    return None
                return payload
            except (json.JSONDecodeError, TypeError):
                return None

        def _forward_payload(self, endpoint: str, payload: dict) -> None:
            try:
                outgoing = dict(payload)
                outgoing["team_id"] = team
                post_json(self.current_console() + endpoint, outgoing)
            except Exception as exc:
                self.get_logger().warning(f"forward {endpoint} failed: {type(exc).__name__}: {exc}")

        def _forward(self, endpoint: str, msg: String) -> None:
            payload = self._decode(msg)
            if payload is not None:
                self._forward_payload(endpoint, payload)

        def on_state(self, msg: String) -> None:
            payload = self._decode(msg)
            if payload is None:
                return
            rover_id = str(payload.get("rover_id") or "").strip()
            if rover_id:
                with self.lock:
                    self.visible_rovers[rover_id] = (dict(payload), time.time())
            self._forward_payload("/api/telemetry", payload)

        def announce_rovers(self) -> None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            except OSError as exc:
                self.get_logger().warning(f"LAN discovery disabled: {exc}")
                return
            targets = [
                ("239.255.73.84", args.discovery_port),
                ("255.255.255.255", args.discovery_port),
                ("127.0.0.1", args.discovery_port),
            ]
            interval = max(0.5, float(args.announce_interval_s))
            try:
                while not self.discovery_stop.wait(interval):
                    now = time.time()
                    with self.lock:
                        rows = list(self.visible_rovers.items())
                        self.visible_rovers = {
                            rover_id: row for rover_id, row in rows if now - row[1] <= 12.0
                        }
                        rows = list(self.visible_rovers.items())
                    for rover_id, (state, _last_seen) in rows:
                        health = state.get("health") if isinstance(state.get("health"), dict) else {}
                        capabilities = [
                            key for key in ("lidar", "camera", "depth", "imu", "localization")
                            if str(health.get(key) or "").upper() not in {"", "UNKNOWN", "OFFLINE"}
                        ]
                        announcement = {
                            "protocol": "hitl-rover-discovery-v1",
                            "gateway_version": "2.1",
                            "agent_port": args.agent_port if self.link_server is not None else None,
                            "team_id": team,
                            "rover_id": rover_id,
                            "name": str(state.get("rover_name") or rover_id),
                            "model": str(state.get("model") or "ROS2 Rover"),
                            "hostname": socket.gethostname(),
                            "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
                            "capabilities": capabilities,
                            "timestamp": now,
                        }
                        encoded = json.dumps(announcement, ensure_ascii=False).encode("utf-8")
                        for target in targets:
                            try:
                                sock.sendto(encoded, target)
                            except OSError:
                                continue
            finally:
                sock.close()

        def stop_discovery(self) -> None:
            self.discovery_stop.set()
            self.discovery_thread.join(timeout=2.5)
            if self.link_server is not None:
                self.link_server.shutdown()
                self.link_server.server_close()
            if self.link_thread is not None:
                self.link_thread.join(timeout=2.5)

        def on_discovery(self, msg: String) -> None:
            self._forward("/api/discovery", msg)

        def on_event(self, msg: String) -> None:
            self._forward("/api/event", msg)

        def on_map(self, msg: String) -> None:
            self._forward("/api/map", msg)

        def on_path(self, msg: String) -> None:
            self._forward("/api/path", msg)

        def on_camera(self, msg: CompressedImage) -> None:
            rover_id = msg.header.frame_id.strip()
            if not rover_id or not msg.data:
                return
            query = urllib.parse.urlencode({"team_id": team, "rover_id": rover_id})
            try:
                post_jpeg(self.current_console() + "/api/camera/frame?" + query, bytes(msg.data))
            except Exception as exc:
                self.get_logger().warning(f"forward camera failed: {type(exc).__name__}: {exc}")

        def pull_commands(self) -> None:
            url = self.current_console() + "/api/commands/pending?" + urllib.parse.urlencode({"team_id": team})
            try:
                commands = get_json(url).get("commands") or []
            except Exception:
                return
            for command in commands:
                command_id = str(command.get("command_id") or "")
                if command_id:
                    self.outbox[command_id] = command
            # The HTTP endpoint is destructive (pop). Keep commands locally
            # until at least one rover-side subscriber is visible in DDS.
            if self.command_pub.get_subscription_count() < 1:
                return
            for command_id, command in list(self.outbox.items()):
                out = String()
                out.data = json.dumps(command, ensure_ascii=False)
                self.command_pub.publish(out)
                self.get_logger().info(f"published directive {command.get('action')} -> {command.get('rover_id')}")
                self.outbox.pop(command_id, None)

    rclpy.init()
    node = Gateway()
    try:
        rclpy.spin(node)
    finally:
        node.stop_discovery()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
