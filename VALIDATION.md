# 验证记录

验证范围覆盖监督平台、局域网访问、二维/立体地图、ROS2 接口、目标搜索、路径规划和安全避障接线。

```bash
python3 -m pytest -q
node --check apps/console/frontend/assets/app.js
bash -n START_MARS_ROVER.sh
python3 -m compileall -q src apps scripts
python3 -m json.tool config/team_config.json
```

当前结果：15 项 Pytest 测试通过；服务监听 `0.0.0.0` 后可从回环地址读取 `/api/health`。小车端自主栈不属于本仓库，其相机、雷达、TF、Nav2 lifecycle 节点和 `/cmd_vel` 接口需在车端项目中验证。
