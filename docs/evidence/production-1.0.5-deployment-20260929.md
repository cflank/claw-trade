# 1.0.5 无加密生产部署

日期：2026-09-29。用户授权部署目标机。

## 发布内容

- 目标：`192.168.1.21`；新版 `claw-trade-production-1.0.5-20260929T073823Z`；回滚 `current-backup` → 1.0.4。
- 手动转发 `send-report-file-via-channel` 不再使用请求里的 `originContextId`，只发当前绑定账号+收件人（防旧页面发给旧微信）。
- 已存账号时刷新二维码恢复为替换流程（`replacementRequired=True`），修复 1.0.4 后改动导致的测试失败。
- 同时正式纳入 1.0.4 部署后在目标机直接修过的内容（单账号不报多账号冲突、换号后清会话）。

## 目标机部署前核查

- 目标机三份 pyc 与 1.0.4 清单哈希不同。反汇编比较：与本地工作区（本轮修改前）逻辑一致，仅 docstring 不同（目标机当时非 optimize=2 编译）。
- 报告锁、数据锁空闲；5175/18789 为 200。

## 构建与部署

- `optimize=2` 编译三份 pyc；增量包 SHA256 `7ea4e0aa07a80f23d81e9d3091212fbcf1871ff04d2fd40c0a10c5e56426cae6`（本地与目标机一致）。
- pyc：channel_bridge `79219bfc…`、channel_text_inbound `ce345ebb…`、routes_ui `d0c34322…`，目标机逐一一致。
- `cp -a` 1.0.4 → 新目录，覆盖增量，清理 `__pycache__`；以 clawtrade 身份导入三模块通过。
- 原子切换 `current`，重启 control/UI；preflight 通过；输出 `DEPLOYED_AND_HEALTHY 1.0.5`，未回滚。

## 运行验证

- control/UI active，`systemctl --failed` 为 0；UI 进程 release root 指向 1.0.5。
- 微信状态（重启后插件就绪）：`state=connected`、`canSendFile=true`、`replacementRequired=false`。
- 重启后账号目录只有一个账号 `ee3401dbac8e…`，旧账号未重新加载。
- 报告列表 `canForwardToChannel=true`、无禁用原因。

## 未验证

- 未做手机端真实转发、确认卡真实收发。须用户在网页转发一份历史报告并确认收到。
