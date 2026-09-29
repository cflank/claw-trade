# 微信换号与确认卡投递修复验收记录

日期：2026-09-23。实施：Luna high；主线程复核；另经独立代码审查。
范围：本地代码与自动化验证，未部署、未重启目标机、未发送微信或触发模型任务。

## 已实现

1. 后台查询已有微信账号；已有账号的二维码刷新返回既有 replacementRequired 状态，复用原账号替换流程。账号库存查询失败不进入普通登录；空账号首次登录保持原流程。
2. 设置页在启用或刷新返回 replacementRequired 时，在同一次操作内进入替换；库存查询失败不显示“已提交重新连接，请重新扫码”。保持既有通道重启后的状态重试策略。
3. 待确认状态保存完整卡片文本。后台发送明确失败、送达未知或异常时留下日志和工作台提示；下一条非确认/取消消息返回原卡内容，不重复生成卡片。
4. 识别失败而未生成卡片时，不虚报“完整确认卡已保留”。确认/取消行为不变。

实现文件：
- src/claw_trade/ui_backend/channel_bridge.py
- src/claw_trade/ui_backend/channel_text_inbound.py
- web/research-ui/src/routes/SettingsPage.tsx

没有修改 OpenClaw、微信插件或报告 guard；没有移除已有单账号发送检查。
现有未提交的定时选股 PDF、费用统计、浏览器脚本等改动保留。

## 验证

- Luna：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_channel_bridge.py tests/unit/ui/test_channel_text_inbound.py`：121 passed。
- Luna 收尾补测：`test_channel_bridge.py`：83 passed，含新增的首次扫码成功落盘后仍正确消费登录结果；未重复运行未再修改的入站测试。
- Luna：在 web/research-ui 执行 `pnpm vitest run src/__tests__/settings-page.test.tsx`：最终 36 passed。覆盖旧页面状态经后台检查转替换、刷新转替换、库存失败不显示扫码成功。
- 主线程：`PYTHONPATH=src .venv/bin/python /tmp/claw-trade-confirmation-recovery-check.py`：通过。真实 ChannelBridge 单账号检查仍通过、双账号仍拒发；工作台失败可见，下一条入站返回完整卡片。仅用隔离状态与现有测试辅助，无真实发送。
- 主线程：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_wechat_reconnect.py tests/unit/ui/test_channel_inbound_bridge.py tests/contracts/test_guard_change_requires_approval.py`：37 passed，1 failed。
- 该失败为 `test_guard_prompt_boundary_policy_is_documented` 引用不存在的 `docs/详细设计.md`。已用 `git cat-file -e HEAD:docs/详细设计.md` 证明 HEAD 中也不存在，实际文档已归档到 `docs/项目文档/归档/原有文档/详细设计.md`。本轮未修改该测试或文档，不掩盖失败。
- 主线程在最终生产源码修改之后重新执行 `pnpm build`：通过，305 modules，输出 `index-N32cARdF.js`。
- `git diff --check`：通过。

独立审查未留下阻断问题。审查时否决了在普通登录轮询阶段“库存非空就强制替换”的建议：首次登录成功本来就会先保存新账号，再由页面消费完成结果；新增回归测试锁定此顺序。

## 未完成的真实验收

按项目既定方式只读检查 9223、9224，两者都无法从 WSL/Windows-local 到达，未新建浏览器或替代会话。
因此没有真实 Chrome 点击或手机收发验收，不能将上述自动化检查称为线上修复验证。目标机仍需部署后再做真实换号与确认卡流程验收。

