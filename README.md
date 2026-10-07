# MARS Rover Earth Ground Station v2.0

这是一个面向**真实实体小车、真实局域网和真实 ROS2 DDS** 的人在环监督平台。平台会自动发现同一局域网中的小车，操作员选择连接后，可分别在总览、地图、感知、任务和诊断页面查看运行状态。

## 系统边界

平台只处理己方车队真实广播的数据：

- 显示己方 Rover 的实时位置、状态、轨迹相关统计。
- 物资点只有在己方 Rover 实际发现并广播后才进入地图。
- 不接收、不保存、不显示敌方 Rover 的位置。
- 不预置物资坐标，不存在全局资源真值接口。
- 不包含虚拟 Rover、仿真环境、Sim2Real、虚拟通信丢包或虚拟比赛运行器。
- 操作员只能下发高层任务指令；底层运动、安全和自主算法仍由实体车负责。
- 操作页面只保留寻找目标、暂停、继续、返航和紧急停车这些已经接入小车的功能。

## 数据链

```text
实体 Rover
      │
      │ ROS2 DDS broadcast
      ▼
/team/<team_id>/fleet_state
/team/<team_id>/resource_discovery
/team/<team_id>/event
/team/<team_id>/path
      │
      ▼
同一局域网中的 Ground Gateway
      ├── UDP 38765 多播/广播心跳（自动发现）
      ├── TCP 38766 连接握手（选择平台回传地址）
      └── HTTP 遥测与指令（可与 Web 平台同机或分机）
      ▼
Earth Ground Station Web UI
      │
      └── /team/<team_id>/operator_directive ──► 实体 Rover
```

## 1. Windows 地面站

```powershell
START_MARS_ROVER.bat
```

平台默认监听 `0.0.0.0:8080`。启动窗口会同时打印本机地址和局域网地址；手机、平板或另一台电脑可打开 `http://平台电脑IP:8080`。

打开平台后先进入“设备连接”。在线小车会像 Wi-Fi 列表一样显示名称、地址、能力和遥测状态，点击“连接”后，平台通过 TCP `38766` 告诉网关把数据回传到当前平台，再进入运行总览。发现心跳使用 UDP `38765`，Web 页面使用 TCP `8080`；系统防火墙需要允许这三个端口在专用局域网内通信。

Linux/ROS2 工作站可同时启动 Web 服务和 DDS 网关：

```bash
bash START_MARS_ROVER.sh
```

启动实体车自主栈的终端也必须设置相同的 Domain，例如默认配置：

```bash
export ROS_DOMAIN_ID=21
ros2 launch wheeltec_nav2 autonomy.launch.py mapping:=false
```

平台用于受信任的小车局域网，操作指令接口没有账户认证，不应映射到公网。`team_id` 中的连字符会在 ROS 2 话题路径中自动规范化为下划线，
例如业务标识 `team-a` 对应 `/team/team_a/*`，JSON 中仍保持 `team-a`。

## 2. 配置己方队伍

编辑：

```text
config/team_config.json
```

例如：

```json
{
  "team_id": "team-a",
  "stale_after_s": 3.0,
  "device_stale_after_s": 7.0,
  "ros_domain_id": 21,
  "discovery_port": 38765,
  "agent_port": 38766,
  "listen_host": "0.0.0.0",
  "port": 8080
}
```

建议两个队伍使用不同的 `ROS_DOMAIN_ID`。`team_id` 是第二层应用过滤，不替代 DDS Domain 隔离。

## 3. 局域网运行方式

推荐方式是在安装了 ROS2 Humble、且与小车处于同一局域网的电脑运行：

```bash
export ROS_DOMAIN_ID=21
bash START_MARS_ROVER.sh
```

该电脑通过 DDS 接收小车数据，同时在 8080 端口提供网页。局域网内其他设备不需要安装 ROS2，只需用浏览器访问启动窗口打印的 LAN 地址。

如果 Web 后端和 ROS2 网关必须分开运行：

```bash
# 平台电脑
python3 apps/console/main.py --host 0.0.0.0 --port 8080

# 能发现小车 DDS 的 ROS2 电脑
export ROS_DOMAIN_ID=21
python3 scripts/ros2_team_gateway.py \
  --team-id team-a \
  --console http://平台电脑IP:8080 \
  --discovery-port 38765 \
  --agent-port 38766
```

两台电脑需要能通过局域网互相访问 TCP 8080、TCP 38766，并允许 UDP 38765 多播/广播；小车与 ROS2 网关必须使用相同 `ROS_DOMAIN_ID`，并允许局域网 UDP/DDS 通信。`--console` 只是启动时的默认地址，用户在设备页连接小车后，网关会自动切换到当前平台地址。即使 UDP 广播受限，平台收到第一条遥测后仍会把该小车加入设备列表。

## 4. 实体车接入

本仓库只包含地面监督平台，不包含小车端导航、识别、探索、避障或底盘代码。小车端按 `docs/ROS2_INTERFACE.md` 发布本队状态、地图、路径、相机和目标发现消息，并订阅高层操作指令。

监督页填写 YOLO 目标类别并点击“开始寻找”后，实体车执行前沿探索；识别到指定类别后取消探索目标，转为 Nav2 目标导航。驾驶舱同时显示识别画面、任务阶段、传感器健康、实时位置和速度、实际轨迹、识别目标及最新规划路径，最终速度命令持续经过安全避障层。

地图支持 `2D / 立体` 切换。立体模式将二维 SLAM 占用栅格中的障碍物抬高显示；它能更直观地查看路线和障碍分布，但数据源仍是二维 `/map`，不等同于 OctoMap 或三维点云建图。

详见：`docs/ROS2_INTERFACE.md`。

目标识别、探索、路径规划和持续避障由小车端已有 ROS2 自主栈执行；平台负责指定目标、显示过程并发送暂停、继续、返航和紧急停车指令。
