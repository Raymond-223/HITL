from pathlib import Path

import pytest

from mars_ground_station import GroundStationService
from scripts.ros2_team_gateway import normalize_console_url, ros_topic_segment


def service(tmp_path: Path) -> GroundStationService:
    return GroundStationService(tmp_path, team_id="blue", stale_after_s=2.0)


def telemetry(rover_id="r1", x=1.0, y=2.0):
    return {"team_id": "blue", "rover_id": rover_id, "pose": {"x": x, "y": y, "yaw_deg": 15}, "mode": "AUTONOMOUS"}


def test_starts_with_zero_world_truth(tmp_path):
    s = service(tmp_path)
    snap = s.snapshot()
    assert snap["rovers"] == []
    assert snap["known_resources"] == []
    assert snap["information_policy"]["opponent_state_available"] is False
    assert snap["information_policy"]["preloaded_resource_truth"] is False
    assert snap["information_policy"]["simulation_enabled"] is False


def test_rejects_other_team(tmp_path):
    s = service(tmp_path)
    row = telemetry()
    row["team_id"] = "red"
    with pytest.raises(ValueError, match="team_id mismatch"):
        s.ingest_telemetry(row)


def test_real_team_telemetry_creates_rover(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    snap = s.snapshot()
    assert [r["rover_id"] for r in snap["rovers"]] == ["r1"]
    assert snap["rovers"][0]["pose"]["x"] == 1.0


def test_resource_exists_only_after_known_rover_reports_it(tmp_path):
    s = service(tmp_path)
    discovery = {"team_id": "blue", "rover_id": "r1", "resource_id": "ore-1", "position": {"x": 3, "y": 4}}
    with pytest.raises(ValueError, match="has not sent telemetry"):
        s.ingest_discovery(discovery)
    s.ingest_telemetry(telemetry())
    s.ingest_discovery(discovery)
    assert s.snapshot()["known_resources"][0]["resource_id"] == "ore-1"


def test_operator_cannot_target_unobserved_rover(tmp_path):
    s = service(tmp_path)
    with pytest.raises(ValueError, match="unknown rover_id"):
        s.queue_operator_command({"rover_id": "enemy", "action": "HOLD"})


def test_secured_event_updates_discovered_target(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    s.ingest_discovery({"team_id": "blue", "rover_id": "r1", "resource_id": "ore-1", "position": {"x": 3, "y": 4}})
    s.ingest_event({"team_id": "blue", "rover_id": "r1", "event_type": "RESOURCE_SECURED", "payload": {"resource_id": "ore-1", "value": 7}})
    assert s.snapshot()["known_resources"][0]["status"] == "SECURED"


def test_pending_commands_are_team_scoped_and_one_shot(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    s.queue_operator_command({"rover_id": "r1", "action": "RETURN"})
    rows = s.pop_pending_commands()
    assert len(rows) == 1 and rows[0]["team_id"] == "blue"
    assert s.pop_pending_commands() == []


def test_search_target_requires_and_normalizes_target_class(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    with pytest.raises(ValueError, match="target_class"):
        s.queue_operator_command({"rover_id": "r1", "action": "SEARCH_TARGET"})
    command = s.queue_operator_command({
        "rover_id": "r1",
        "action": "SEARCH_TARGET",
        "payload": {"target_class": " Person "},
    })
    assert command["payload"]["target_class"] == "person"


def test_real_nav_path_is_stored_for_map_overlay(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    s.ingest_path({
        "team_id": "blue",
        "rover_id": "r1",
        "frame_id": "map",
        "points": [{"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 3.0}],
    })
    assert s.snapshot()["paths"]["r1"]["points"][-1] == {"x": 2.0, "y": 3.0}


def test_team_id_is_normalized_only_for_ros_topic_path():
    assert ros_topic_segment("team-a") == "team_a"
    assert ros_topic_segment("火星 team/a") == "___team_a"


def test_telemetry_builds_per_rover_trajectory_and_preserves_speed(tmp_path):
    s = service(tmp_path)
    first = telemetry(x=0.0, y=0.0)
    first.update({"linear_mps": 0.42, "angular_rps": -0.15})
    s.ingest_telemetry(first)
    s.ingest_telemetry(telemetry(x=0.2, y=0.1))
    snap = s.snapshot()
    assert len(snap["trajectories"]["r1"]) == 2
    assert snap["rovers"][0]["linear_mps"] == pytest.approx(0.42)
    assert snap["rovers"][0]["angular_rps"] == pytest.approx(-0.15)


def test_real_map_is_team_scoped_and_rle_validated(tmp_path):
    s = service(tmp_path)
    s.ingest_telemetry(telemetry())
    row = {
        "team_id": "blue", "rover_id": "r1", "width": 3, "height": 2,
        "resolution": 0.05, "origin": {"x": -1.0, "y": -2.0},
        "runs": [[-1, 2], [0, 3], [100, 1]],
    }
    s.ingest_map(row)
    stored = s.map_for("r1")
    assert stored is not None and stored["runs"] == row["runs"]
    assert s.snapshot()["maps"]["r1"]["width"] == 3
    bad = dict(row, runs=[[0, 2]])
    with pytest.raises(ValueError, match="does not match"):
        s.ingest_map(bad)


def test_camera_frame_requires_observed_team_rover(tmp_path):
    s = service(tmp_path)
    jpeg = b"\xff\xd8test-frame\xff\xd9"
    with pytest.raises(ValueError, match="has not sent telemetry"):
        s.ingest_camera_frame("blue", "r1", jpeg)
    s.ingest_telemetry(telemetry())
    s.ingest_camera_frame("blue", "r1", jpeg)
    frame = s.camera_frame("r1")
    assert frame is not None and frame[0] == jpeg
    assert "r1" in s.snapshot()["camera_feeds"]


def test_lan_announcement_discovers_and_connects_rover(tmp_path):
    s = service(tmp_path)
    device = s.ingest_network_announcement({
        "protocol": "hitl-rover-discovery-v1",
        "team_id": "blue",
        "rover_id": "r1",
        "name": "实验小车",
        "hostname": "rover-one",
        "ros_domain_id": 21,
        "agent_port": 38766,
        "capabilities": ["camera", "lidar"],
    }, "192.168.8.31")
    assert device["online"] is True
    assert device["ip_address"] == "192.168.8.31"
    assert device["capabilities"] == ["camera", "lidar"]
    assert device["agent_port"] == 38766
    row = telemetry()
    row["_source_ip"] = "127.0.0.1"
    row["health"] = {"camera": "OK"}
    s.ingest_telemetry(row)
    merged = s.snapshot()["devices"][0]
    assert merged["ip_address"] == "192.168.8.31"
    assert merged["capabilities"] == ["camera", "lidar"]
    connected = s.connect_rover("r1")
    assert connected["rover_id"] == "r1"
    assert s.snapshot()["connected_rover_id"] == "r1"
    s.disconnect_rover("r1")
    assert s.snapshot()["connected_rover_id"] is None


def test_telemetry_also_registers_device_when_udp_is_unavailable(tmp_path):
    s = service(tmp_path)
    row = telemetry()
    row["_source_ip"] = "10.0.0.22"
    row["health"] = {"camera": "OK", "lidar": "OK"}
    s.ingest_telemetry(row)
    device = s.snapshot()["devices"][0]
    assert device["rover_id"] == "r1"
    assert device["ip_address"] == "10.0.0.22"
    assert device["telemetry_online"] is True


def test_lan_announcement_is_team_scoped(tmp_path):
    s = service(tmp_path)
    with pytest.raises(ValueError, match="team_id mismatch"):
        s.ingest_network_announcement({
            "protocol": "hitl-rover-discovery-v1",
            "team_id": "red",
            "rover_id": "r1",
        }, "192.168.8.31")


def test_console_callback_url_is_normalized_and_rejects_credentials():
    assert normalize_console_url("http://192.168.8.10:8080/") == "http://192.168.8.10:8080"
    with pytest.raises(ValueError, match="credentials"):
        normalize_console_url("http://user:secret@192.168.8.10:8080")
