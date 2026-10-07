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


def main() -> int:
    parser = argparse.ArgumentParser(description="ROS2 team broadcast <-> local MARS ground station")
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--console", default="http://127.0.0.1:8080")
    parser.add_argument("--command-poll-s", type=float, default=0.25)
    parser.add_argument("--discovery-port", type=int, default=38765)
    parser.add_argument("--announce-interval-s", type=float, default=2.0)
    args = parser.parse_args()

    try:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import CompressedImage
        from std_msgs.msg import String
    except Exception as exc:
        raise SystemExit(f"ROS 2 Humble Python environment required: {exc}")

    base = args.console.rstrip("/")
    team = args.team_id.strip()
    topic_team = ros_topic_segment(team)
    prefix = f"/team/{topic_team}"

    class Gateway(Node):
        def __init__(self) -> None:
            super().__init__(f"mars_ground_gateway_{topic_team}")
            self.lock = threading.Lock()
            self.create_subscription(String, f"{prefix}/fleet_state", self.on_state, 50)
            self.create_subscription(String, f"{prefix}/resource_discovery", self.on_discovery, 50)
            self.create_subscription(String, f"{prefix}/event", self.on_event, 50)
            self.create_subscription(String, f"{prefix}/map", self.on_map, 10)
            self.create_subscription(String, f"{prefix}/path", self.on_path, 20)
            self.create_subscription(CompressedImage, f"{prefix}/camera/compressed", self.on_camera, 10)
            self.command_pub = self.create_publisher(String, f"{prefix}/operator_directive", 50)
            self.outbox: dict[str, dict] = {}
            self.visible_rovers: dict[str, tuple[dict, float]] = {}
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
                post_json(base + endpoint, outgoing)
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
                post_jpeg(base + "/api/camera/frame?" + query, bytes(msg.data))
            except Exception as exc:
                self.get_logger().warning(f"forward camera failed: {type(exc).__name__}: {exc}")

        def pull_commands(self) -> None:
            url = base + "/api/commands/pending?" + urllib.parse.urlencode({"team_id": team})
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
