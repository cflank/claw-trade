# 1.0.4 无加密生产部署

日期：2026-09-26。

## 发布内容

- 目标：`192.168.1.21`，网页 `http://192.168.1.21:5175/`。
- 新版：`claw-trade-production-1.0.4-20260926T215500Z`。
- 回滚版本：`claw-trade-production-1.0.3-20260923T144533Z`，完整保留。
- 版本声明：`pyproject.toml`、`uv.lock` 更新至 1.0.4；运行版本由生产 release 名称确定。
- 只发布本次转发按钮优化和状态验证修复。

## 代码变更

| 文件 | 变更 |
|------|------|
| `routes_ui.py` | 新增 `_get_forward_reason()` 判断多账号/未连接/无文件能力并返回对应提示；每个报告项添加 `forwardDisabledReason` 字段 |
| `HistoryRail.tsx` | 始终显示转发按钮，禁用时显示 `is-disabled` 类和 title 原因 |
| `contracts.ts` | 添加 `forwardDisabledReason` 到 `SavedReportForUser` |
| `HomePage.tsx` | 手动转发使用 `originContextId: null` 强制使用当前绑定会话 |
| `SettingsPage.tsx` | 取消/恢复后验证真实状态，根据 `replacementRequired`/`canSendFile`/`state` 显示不同提示 |
| `styles.css` | 添加 `.ct-history-forward.is-disabled` 样式 |

## 验证

- 后端测试：**122 passed**
- 前端 settings-page 测试：**36 passed**
- 前端 build：**通过**（305 modules）
- overlay 哈希：
  - channel_bridge.pyc: `8c450cbfb458b220fdd4481130d3a5bce0da77c21a12aa581df92af2c261cd16`
  - channel_text_inbound.pyc: `38978f5df931d9bcd3ed514ae4fd1dfbff898d3edb726536077585739900471e`
  - routes_ui.pyc: `e38eda4143ab7b05a867ab043e7be8e365b38c4a8fd263e399b15f1f0209d0d0`

## 部署状态

- 构建完成：2026-09-26 21:55
- 部署完成：2026-09-26 22:25
- 服务状态：active, no failed units
- UI 确认：`index-BP910Fj7.js` 已加载
- 等待用户执行账号清理事务
