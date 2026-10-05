from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RoverMode(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    HOLD = "HOLD"
    RETURN = "RETURN"
    ESTOP = "ESTOP"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class Pose2D:
    x: float = 0.0
    y: float = 0.0
    yaw_deg: float = 0.0


@dataclass(slots=True)
class RoverState:
    rover_id: str
    team_id: str
    pose: Pose2D = field(default_factory=Pose2D)
    mode: str = RoverMode.UNKNOWN.value
    battery_pct: float | None = None
    linear_mps: float | None = None
    angular_rps: float | None = None
    mission: str | None = None
    health: dict[str, Any] = field(default_factory=dict)
    source_timestamp: float | None = None
    received_at: float = 0.0
    distance_m: float = 0.0
    message_count: int = 0

    def to_dict(self, now: float, stale_after_s: float) -> dict[str, Any]:
        row = asdict(self)
        age = max(0.0, now - self.received_at) if self.received_at else None
        row["age_s"] = age
        row["online"] = bool(age is not None and age <= stale_after_s)
        return row


@dataclass(slots=True)
class KnownResource:
    resource_id: str
    x: float
    y: float
    discovered_by: str
    first_seen_at: float
    last_seen_at: float
    confidence: float | None = None
    resource_type: str | None = None
    status: str = "DISCOVERED"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
