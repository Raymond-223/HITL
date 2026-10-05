# Architecture

## 1. 定位

本系统是“地球端”人在环监督站。比赛中的信息可见性由物理传感器和己方通信决定，而不是由软件隐藏一个内部全局真值。

## 2. Trust boundary

Ground Station 可接收：

1. 己方 Rover 自身状态。
2. 己方 Rover 发现的物资。
3. 己方 Rover 任务/安全事件。

Ground Station 不定义、也不提供：

1. 敌方车辆状态。
2. 未发现物资位置。
3. 仿真世界状态。
4. 裁判全局真值。

## 3. 人在环边界

监督页面允许操作员发送 `SEARCH_TARGET / HOLD / RESUME / RETURN / EMERGENCY_STOP` 高层意图。

这些消息只进入实体车自主系统的任务层。平台不连续发送速度控制量，不替代本车规划器和安全控制器。
