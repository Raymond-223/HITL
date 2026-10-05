"""Authoritative ground-side store for real-rover supervision.

There is deliberately no simulation, opponent model, preloaded resource truth,
or synthetic rover state. The store learns only from messages produced by the
configured team and from operator actions taken through the console.
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import KnownResource, Pose2D, RoverState


class GroundStationService:
    ALLOWED_ACTIONS = {
        "HOLD", "RESUME", "GO_TO", "SEARCH_AREA", "SEARCH_TARGET",
        "RETURN", "EMERGENCY_STOP",
    }

    def __init__(self, workspace: Path, team_id: str = "team-a", stale_after_s: float = 3.0) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.team_id = team_id.strip() or "team-a"
        self.stale_after_s = max(0.5, float(stale_after_s))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._revision = 0
        self.rovers: dict[str, RoverState] = {}
        self.resources: dict[str, KnownResource] = {}
        self.events: list[dict[str, Any]] = []
        self.pending_commands: list[dict[str, Any]] = []
        self._last_pose: dict[str, tuple[float, float]] = {}
        self._last_track_at: dict[str, float] = {}
        self.trajectories: dict[str, list[dict[str, float]]] = {}
        self.maps: dict[str, dict[str, Any]] = {}
        self.paths: dict[str, dict[str, Any]] = {}
        self.camera_frames: dict[str, tuple[bytes, float]] = {}

    def _assert_team(self, payload: dict[str, Any]) -> None:
        incoming = str(payload.get("team_id") or "").strip()
        if incoming != self.team_id:
            raise ValueError(f"team_id mismatch: expected {self.team_id!r}")

    @staticmethod
    def _number(value: Any, *, default: float | None = None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _changed(self) -> None:
        self._revision += 1
        self._persist_state()
        self._condition.notify_all()

    def _persist_state(self) -> None:
        target = self.workspace / "ground_station_state.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    def _record_event(self, event_type: str, *, rover_id: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "team_id": self.team_id,
            "rover_id": rover_id,
            "timestamp": time.time(),
            "payload": payload or {},
        }
        self.events.append(row)
        self.events = self.events[-1000:]
        return row

    def ingest_telemetry(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._assert_team(payload)
        rover_id = str(payload.get("rover_id") or "").strip()
        if not rover_id:
            raise ValueError("rover_id is required")
        pose_raw = payload.get("pose") if isinstance(payload.get("pose"), dict) else {}
        x = self._number(pose_raw.get("x"), default=0.0) or 0.0
        y = self._number(pose_raw.get("y"), default=0.0) or 0.0
        yaw = self._number(pose_raw.get("yaw_deg"), default=0.0) or 0.0
        now = time.time()
        with self._condition:
            existing = self.rovers.get(rover_id)
            if existing is None:
                existing = RoverState(rover_id=rover_id, team_id=self.team_id)
                self.rovers[rover_id] = existing
                self._record_event("ROVER_FIRST_SEEN", rover_id=rover_id)
            previous = self._last_pose.get(rover_id)
            if previous is not None:
                step = math.hypot(x - previous[0], y - previous[1])
                if math.isfinite(step) and step <= 20.0:
                    existing.distance_m += step
            self._last_pose[rover_id] = (x, y)
            track = self.trajectories.setdefault(rover_id, [])
            last_track_at = self._last_track_at.get(rover_id, 0.0)
            track_step = math.inf if not track else math.hypot(x - track[-1]["x"], y - track[-1]["y"])
            if not track or track_step >= 0.05 or now - last_track_at >= 1.0:
                track.append({"x": x, "y": y, "timestamp": now})
                del track[:-2000]
                self._last_track_at[rover_id] = now
            existing.pose = Pose2D(x=x, y=y, yaw_deg=yaw)
            existing.mode = str(payload.get("mode") or existing.mode)
            existing.battery_pct = self._number(payload.get("battery_pct"), default=existing.battery_pct)
            existing.linear_mps = self._number(payload.get("linear_mps"), default=existing.linear_mps)
            existing.angular_rps = self._number(payload.get("angular_rps"), default=existing.angular_rps)
            existing.mission = str(payload.get("mission")) if payload.get("mission") is not None else existing.mission
            if isinstance(payload.get("health"), dict):
                existing.health = dict(payload["health"])
            existing.source_timestamp = self._number(payload.get("timestamp"), default=now)
            existing.received_at = now
            existing.message_count += 1
            self._changed()
            return existing.to_dict(now, self.stale_after_s)

    def ingest_map(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store the latest real occupancy grid for one observed rover."""
        self._assert_team(payload)
        rover_id = str(payload.get("rover_id") or "").strip()
        if not rover_id or rover_id not in self.rovers:
            raise ValueError("map rejected: rover has not sent telemetry to this team station")
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        resolution = self._number(payload.get("resolution"))
        origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
        runs = payload.get("runs")
        if width <= 0 or height <= 0 or width * height > 4_000_000:
            raise ValueError("invalid map dimensions")
        if resolution is None or resolution <= 0.0:
            raise ValueError("invalid map resolution")
        if not isinstance(runs, list) or len(runs) > width * height:
            raise ValueError("invalid map run-length data")
        total = 0
        normalized_runs: list[list[int]] = []
        for run in runs:
            if not isinstance(run, list) or len(run) != 2:
                raise ValueError("each map run must be [value, count]")
            value, count = int(run[0]), int(run[1])
            if value < -1 or value > 100 or count <= 0:
                raise ValueError("invalid map run")
            total += count
            normalized_runs.append([value, count])
        if total != width * height:
            raise ValueError("map data length does not match dimensions")
        row = {
            "rover_id": rover_id,
            "width": width,
            "height": height,
            "resolution": resolution,
            "origin": {
                "x": self._number(origin.get("x"), default=0.0) or 0.0,
                "y": self._number(origin.get("y"), default=0.0) or 0.0,
                "yaw_deg": self._number(origin.get("yaw_deg"), default=0.0) or 0.0,
            },
            "runs": normalized_runs,
            "timestamp": self._number(payload.get("timestamp"), default=time.time()),
            "received_at": time.time(),
        }
        with self._condition:
            self.maps[rover_id] = row
            self._changed()
        return {key: value for key, value in row.items() if key != "runs"}

    def map_for(self, rover_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.maps.get(rover_id)
            return dict(row) if row is not None else None

    def ingest_path(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store the latest real Nav2 global path for one observed rover."""
        self._assert_team(payload)
        rover_id = str(payload.get("rover_id") or "").strip()
        if not rover_id or rover_id not in self.rovers:
            raise ValueError("path rejected: rover has not sent telemetry to this team station")
        raw_points = payload.get("points")
        if not isinstance(raw_points, list) or len(raw_points) > 2000:
            raise ValueError("invalid path points")
        points: list[dict[str, float]] = []
        for point in raw_points:
            if not isinstance(point, dict):
                raise ValueError("invalid path point")
            x = self._number(point.get("x"))
            y = self._number(point.get("y"))
            if x is None or y is None:
                raise ValueError("path point must contain finite x/y")
            points.append({"x": x, "y": y})
        row = {
            "rover_id": rover_id,
            "frame_id": str(payload.get("frame_id") or "map"),
            "timestamp": self._number(payload.get("timestamp"), default=time.time()),
            "received_at": time.time(),
            "points": points,
        }
        with self._condition:
            self.paths[rover_id] = row
            self._changed()
        return {key: value for key, value in row.items() if key != "points"}

    def ingest_camera_frame(self, team_id: str, rover_id: str, frame: bytes) -> None:
        if team_id.strip() != self.team_id:
            raise ValueError(f"team_id mismatch: expected {self.team_id!r}")
        rover_id = rover_id.strip()
        if not rover_id or rover_id not in self.rovers:
            raise ValueError("camera rejected: rover has not sent telemetry to this team station")
        if not frame or len(frame) > 2_500_000 or not frame.startswith(b"\xff\xd8"):
            raise ValueError("invalid or oversized JPEG frame")
        with self._condition:
            self.camera_frames[rover_id] = (bytes(frame), time.time())
            self._revision += 1
            self._condition.notify_all()

    def camera_frame(self, rover_id: str) -> tuple[bytes, float] | None:
        with self._lock:
            return self.camera_frames.get(rover_id)

    def ingest_discovery(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._assert_team(payload)
        rover_id = str(payload.get("rover_id") or "").strip()
        resource_id = str(payload.get("resource_id") or "").strip()
        if not rover_id or not resource_id:
            raise ValueError("rover_id and resource_id are required")
        if rover_id not in self.rovers:
            raise ValueError("discovery rejected: rover has not sent telemetry to this team station")
        pos = payload.get("position") if isinstance(payload.get("position"), dict) else {}
        x = self._number(pos.get("x"))
        y = self._number(pos.get("y"))
        if x is None or y is None:
            raise ValueError("discovery position.x and position.y are required")
        now = time.time()
        confidence = self._number(payload.get("confidence"))
        with self._condition:
            first = resource_id not in self.resources
            if first:
                item = KnownResource(
                    resource_id=resource_id,
                    x=x,
                    y=y,
                    discovered_by=rover_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    confidence=confidence,
                    resource_type=str(payload.get("resource_type")) if payload.get("resource_type") else None,
                    metadata=dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {},
                )
                self.resources[resource_id] = item
            else:
                item = self.resources[resource_id]
                item.x, item.y, item.last_seen_at = x, y, now
                if confidence is not None:
                    item.confidence = confidence
            self._record_event("RESOURCE_DISCOVERED" if first else "RESOURCE_UPDATED", rover_id=rover_id, payload={
                "resource_id": resource_id,
                "x": x,
                "y": y,
                "confidence": confidence,
            })
            self._changed()
            return item.to_dict()

    def ingest_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._assert_team(payload)
        rover_id = str(payload.get("rover_id") or "").strip() or None
        event_type = str(payload.get("event_type") or "ROVER_EVENT").strip().upper()
        details = dict(payload.get("payload") or {}) if isinstance(payload.get("payload"), dict) else {}
        with self._condition:
            if event_type == "RESOURCE_SECURED":
                resource_id = str(details.get("resource_id") or "")
                if resource_id in self.resources:
                    self.resources[resource_id].status = "SECURED"
            row = self._record_event(event_type, rover_id=rover_id, payload=details)
            self._changed()
            return row

    def queue_operator_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        rover_id = str(payload.get("rover_id") or "").strip()
        action = str(payload.get("action") or "").strip().upper()
        if not rover_id:
            raise ValueError("rover_id is required")
        if rover_id not in self.rovers:
            raise ValueError("unknown rover_id; commands can only target rovers observed from this team")
        if action not in self.ALLOWED_ACTIONS:
            raise ValueError(f"unsupported action: {action}")
        command_payload = (
            dict(payload.get("payload") or {})
            if isinstance(payload.get("payload"), dict) else {}
        )
        if action == "SEARCH_TARGET":
            target_class = str(command_payload.get("target_class") or "").strip().lower()
            if not target_class or len(target_class) > 64:
                raise ValueError("SEARCH_TARGET requires a valid target_class")
            command_payload["target_class"] = target_class
        command = {
            "command_id": uuid.uuid4().hex,
            "team_id": self.team_id,
            "rover_id": rover_id,
            "action": action,
            "payload": command_payload,
            "created_at": time.time(),
        }
        with self._condition:
            self.pending_commands.append(command)
            self._record_event("OPERATOR_DIRECTIVE", rover_id=rover_id, payload={"action": action, "command_id": command["command_id"]})
            self._changed()
        return command

    def pop_pending_commands(self) -> list[dict[str, Any]]:
        with self._condition:
            rows = list(self.pending_commands)
            self.pending_commands.clear()
            if rows:
                self._changed()
            return rows

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "revision": self._revision,
            "server_time": now,
            "team_id": self.team_id,
            "information_policy": {
                "own_team_only": True,
                "opponent_state_available": False,
                "preloaded_resource_truth": False,
                "simulation_enabled": False,
            },
            "rovers": [row.to_dict(now, self.stale_after_s) for row in sorted(self.rovers.values(), key=lambda x: x.rover_id)],
            "known_resources": [row.to_dict() for row in sorted(self.resources.values(), key=lambda x: x.resource_id)],
            "events": list(reversed(self.events[-100:])),
            "pending_command_count": len(self.pending_commands),
            "trajectories": {key: list(value) for key, value in self.trajectories.items()},
            "maps": {
                key: {field: value for field, value in row.items() if field != "runs"}
                for key, row in self.maps.items()
            },
            "paths": {key: dict(value) for key, value in self.paths.items()},
            "camera_feeds": {
                key: {"received_at": value[1], "age_s": max(0.0, now - value[1])}
                for key, value in self.camera_frames.items()
            },
        }

    def wait_for_revision(self, after: int, timeout: float = 15.0) -> dict[str, Any]:
        with self._condition:
            if self._revision <= after:
                self._condition.wait(timeout=max(0.1, timeout))
            return self.snapshot()
