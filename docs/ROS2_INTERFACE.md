# ROS2 / DDS 接口合同

## Team topics

所有跨车、跨地面站消息均限制在：

```text
/team/<team_id>/fleet_state
/team/<team_id>/resource_discovery
/team/<team_id>/event
/team/<team_id>/path
/team/<team_id>/operator_directive
```

当前参考实现使用 `std_msgs/msg/String` 携带 JSON，便于你们先接通真实车；接口稳定后可进一步改成自定义 ROS2 msg。

注意：ROS 2 话题名称不允许连字符。实现会仅在 DDS 话题路径中把非字母数字字符替换为下划线；
例如 `team_id=team-a` 使用 `/team/team_a/*`，消息 JSON 内仍为 `team-a`。

## fleet_state

每辆实体车周期广播：

```json
{
  "team_id": "team-a",
  "rover_id": "rover-01",
  "timestamp": 1789540201.21,
  "pose": {"x": 3.82, "y": 7.31, "yaw_deg": 81.4},
  "linear_mps": 0.42,
  "angular_rps": 0.08,
  "mode": "AUTONOMOUS",
  "battery_pct": 78,
  "mission": "SEARCH_SECTOR_B",
  "health": {"lidar": "OK", "camera": "OK"}
}
```

Ground Station 不关心 `pose` 来自 AMCL、SLAM、RTK、VIO 或融合定位，只要求坐标系在己方车队内部一致。

## resource_discovery

只有实体车真正发现物资后发送：

```json
{
  "team_id": "team-a",
  "rover_id": "rover-02",
  "resource_id": "resource-017",
  "position": {"x": 8.42, "y": 3.15},
  "resource_type": "sample",
  "confidence": 0.91,
  "timestamp": 1789540201.21
}
```

Ground Station 启动时资源数据库为空。不存在“先保存坐标、前端不显示”的实现。

## event

```json
{
  "team_id": "team-a",
  "rover_id": "rover-02",
  "event_type": "RESOURCE_SECURED",
  "payload": {"resource_id": "resource-017", "value": 10}
}
```

用于刷新平台事件流和目标状态。推荐事件：`RESOURCE_SECURED`、`TARGET_FOUND`、`TARGET_REACHED`、`SAFETY_STOP`、`COLLISION_WARNING`、`ESTOP`、`MISSION_COMPLETED`。

## operator_directive

```json
{
  "command_id": "...",
  "team_id": "team-a",
  "rover_id": "rover-02",
  "action": "SEARCH_AREA",
  "payload": {"polygon": [[0,0],[3,0],[3,2],[0,2]]},
  "created_at": 1789540201.21
}
```

实体车必须再次验证 `team_id`、`rover_id`，并由自身安全状态机决定是否接受。

搜索指定识别类别：

```json
{
  "action": "SEARCH_TARGET",
  "payload": {"target_class": "person"}
}
```

`target_class` 默认使用 YOLO11 COCO 英文类别名，也允许换用自定义模型后的类别名。

## path

实体车把 Nav2 最新全局规划路径广播给平台：

```json
{
  "team_id": "team-a",
  "rover_id": "rover-02",
  "frame_id": "map",
  "points": [{"x": 1.0, "y": 2.0}, {"x": 1.2, "y": 2.1}]
}
```

## Network isolation

推荐：

```bash
# Team A
export ROS_DOMAIN_ID=21

# Team B
export ROS_DOMAIN_ID=22
```

这可以在 DDS discovery 层避免地面站发现另一组车的 topic。`team_id` 过滤是额外保险。

## LAN discovery heartbeat

平台网关会根据 `fleet_state` 自动发送局域网发现心跳，不要求小车自主栈新增 UDP 代码：

```json
{
  "protocol": "hitl-rover-discovery-v1",
  "gateway_version": "2.1",
  "agent_port": 38766,
  "team_id": "team-a",
  "rover_id": "rover-01",
  "name": "rover-01",
  "hostname": "wheeltec",
  "ros_domain_id": "21",
  "capabilities": ["lidar", "camera", "imu", "localization"]
}
```

默认目的端口为 UDP `38765`，多播组为 `239.255.73.84`。网关的连接代理默认监听 TCP `38766`；平台选择设备时通过它自动配置回传地址。平台仍会把正常到达的 `fleet_state` 作为发现信号，因此广播不可用时数据链不会失效。
