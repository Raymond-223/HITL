# 快速接入指南

1. 地面电脑运行 `START_MARS_ROVER.bat`；Linux/ROS2 电脑运行 `bash START_MARS_ROVER.sh`。
2. 修改 `config/team_config.json` 中的 `team_id` 与本队 `ROS_DOMAIN_ID`。
3. ROS2 地面电脑与实体小车接入同一局域网，并设置相同 `ROS_DOMAIN_ID`。
4. 按 `docs/ROS2_INTERFACE.md` 让小车端自主栈发布平台需要的话题，并订阅操作指令。本仓库不包含小车端代码。
5. 地面电脑运行：

```bash
export ROS_DOMAIN_ID=21
python3 scripts/ros2_team_gateway.py --team-id team-a
```

Linux 脚本会同时启动网页服务和 DDS 网关，并打印 `http://局域网IP:8080`。同一局域网的手机、平板和其他电脑可直接打开该地址。实体车启动自主栈前同样要执行 `export ROS_DOMAIN_ID=21`。

6. 当车辆真实广播状态后，地图才会出现车辆；当车辆真实广播目标发现后，地图才会出现目标。
7. 网页选择在线车辆，填写目标类别并点击“开始寻找”。小车会探索、识别目标、
   规划到目标附近并持续通过安全层避障。
8. 地图右上角可切换 `2D / 立体`。立体视图来自二维 SLAM 栅格，不是三维点云地图。
