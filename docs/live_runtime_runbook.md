# Live Runtime Runbook

仅用于 live/fresh/provider run 前置门禁。先过门禁，再派发 live-run。

## 0) 固定口径（先打印）

- 启动脚本：`scripts/start-control-runtime.sh`
- 测试入口：`scripts/start-control-runtime.sh -- <test-or-live-command>`
- 环境：claw-trade 仓 `uv` 环境
- OpenViking：`127.0.0.1:1933`
- OpenClaw gateway：`127.0.0.1:18789`
- 模式：no-sidecar（不启 invest sidecar）
- OpenViking 源配置：`~/.openviking/ov.conf`
- 运行时写入：`.runtime/dev-services`（config/data/cache）

## 1) 路径 A：复用已有 runtime（优先）

1. 检查 `runtime.env` 存在。
2. 检查 `http://127.0.0.1:1933/health`（或 `/healthz`）可用。
3. 检查 `http://127.0.0.1:18789/health` 可用。
4. 三项都通过则直接复用，禁止盲目重启。

## 2) 路径 B：必须重启时

先输出 preflight 表并逐项确认：

| 检查项 | 期望 |
| --- | --- |
| `CLAW_TRADE_OPENVIKING_MCP_MODULE` | 未设置或显式 `unset` |
| `CLAW_TRADE_OPENVIKING_MCP_CWD` | 未设置或显式 `unset` |
| `CLAW_TRADE_OPENVIKING_SERVER_BIN` | 未设置或显式 `unset`（除非人类批准 external server） |
| `CLAW_TRADE_OPENVIKING_SERVER_CWD` | 未设置或显式 `unset`（除非人类批准 external server） |
| `OPENVIKING_CONFIG_FILE` | `.runtime/dev-services/openviking/ov.conf` |
| `OPENVIKING_DATA_DIR` | `.runtime/dev-services/openviking/data` |
| `CLAW_TRADE_OPENVIKING_MCP_STARTED` | `runtime.env` 生成后为 `0` |
| `1933 health` | 通过 |
| `18789 health` | 通过 |

重启后若仍失败，必须按 memory 口径排查并记录：端口占用、沙箱/loopback 权限、覆盖变量、旧 PID、脚本清理、provider。

## 3) 测试命令模式

需要从干净 runtime 开始的测试或 fresh run，统一使用：

```bash
scripts/start-control-runtime.sh -- uv run pytest <tests>
scripts/start-control-runtime.sh -- uv run python -m claw_trade.cli.run_control ...
```

脚本会先启动 OpenViking 和 OpenClaw，写入并导出 `.runtime/dev-services/runtime.env` 中的运行变量，执行 `--` 后面的命令；命令结束、失败或收到中断信号时，脚本必须关闭本次拉起的 OpenViking/OpenClaw 服务。

## 4) 禁止项

- 不得改用其他启动法绕过。
- 不得启动 invest sidecar 绕过。
- 不得用 `/tmp` 临时配置绕过。
- 不得从 source tree 猜测口径替代 memory 固定口径。
- 不得在需要干净 runtime 的测试前手动分别启动 OpenViking 或 OpenClaw；必须走 `scripts/start-control-runtime.sh -- <command>`。
- 不得用 mock/stub/fake/capture-only 冒充 live runtime readiness。

## 4) 责任边界

- C/live-run sub-agent 不得自行解释 runtime readiness。
- 必须由总管先验 preflight gate 并打印 preflight 表。
- 缺 preflight 表的 live run 证据无效。
