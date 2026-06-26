# 工控机第一版 Ubuntu 虚拟机创建记录

- 创建日期：2026-06-26
- 宿主机：待填写
- VM 名称：claw-trade-prod-vm
- Ubuntu 镜像 URL：待填写
- Ubuntu 镜像 sha256：待填写
- CPU：4 核目标配置
- 内存：8G 目标配置
- 磁盘：80G 目标配置
- VM IP：待填写
- SSH 用户：deploy 或实际测试机用户
- 服务用户：clawtrade, clawkiosk
- 基线快照：clean-ubuntu-24.04-before-claw-trade
- 验证命令：待填写
- 验证结果：未完成

## 当前备注

当前已有独立 Ubuntu 测试机可用于部署验证时，也可以先记录实际机器 IP、SSH 用户和验证命令。
无论使用 VM 还是真实工控机，都不能把“可 SSH 登录”当成 Virbox SDK/runtime 验证通过。
