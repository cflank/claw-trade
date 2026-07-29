# claw-trade

claw-trade 是面向中文投资者的多智能体投研系统。它按 TradingAgents / TradingAgents-CN 的分析流程组织市场、基本面、新闻、社交情绪、投资辩论、交易、风险和组合决策，最终输出带证据链的中文投研报告。

## 产品能力

- `/report`：启动完整投研报告流程。
- `/select`：执行选股与候选确认流程。
- 普通聊天：不自动进入报告工作流。
- 支持市场：A 股、美股、港股和加密资产；各市场必须使用已批准的数据与提示词策略。
- 证据约束：不伪造行情、新闻、财务指标、估值、目标价、图表、来源或工具执行结果。

## 工作方式

claw-trade 控制工作流顺序，OpenClaw 每次运行一个真实 agent turn。12 个 worker 依次完成：

```text
市场 / 基本面 / 新闻 / 社交分析
  -> 多空研究辩论
  -> 研究经理决策
  -> 交易员方案
  -> 风险辩论
  -> 组合经理最终决策
```

每个 worker 的提示词和技能位于 `agents/<worker>/`。阶段产物以可追溯的报告和运行证据传递，Python 只负责调度、校验和导出，不代替 worker 写投资结论。

## 目录

```text
agents/                  worker 配置、提示词和技能
src/claw_trade/          工作流、数据层、授权、报告与 Web 后端
web/research-ui/         Research UI
third_party/openclaw/    OpenClaw 单智能体运行时
data/                    出厂历史数据包
packaging/production/    生产安装与 systemd 资产
scripts/                 本地运行、数据和生产脚本
tests/                   单元、契约、集成和 UI 测试
```

## 本地运行

要求 Python 3.12、`uv`、Node.js 和 `pnpm`。

启动完整本地控制运行时：

```bash
scripts/start-control-runtime.sh
```

启动 Research UI：

```bash
scripts/start-research-ui.sh
```

前端代码变化后先构建：

```bash
pnpm --dir web/research-ui build
scripts/start-research-ui.sh
```

## 数据

当前默认出厂数据包：

```text
data/current-seed-20260715.tar
```

恢复、校验和制作说明见 [data/README.md](data/README.md)。生产报告所需的第三方数据源通过环境变量配置；没有可用数据源时系统应明确失败，不生成假数据。

## 生产交付

产品版本以 `pyproject.toml` 的 `[project].version` 为唯一来源，当前版本为 `1.0.0`。生产包不会提交到 Git。

生产包结构、安装、授权和服务说明见 [packaging/production/README_FACTORY_TEST.md](packaging/production/README_FACTORY_TEST.md)。生产安装已接入 Virbox 状态检查和授权门；授权不可用时，受保护功能会被阻止。

## 测试

```bash
uv run pytest
```

涉及 OpenClaw runtime 的修改还必须重新构建、重启并通过真实 provider payload 验证；静态测试不能替代真实运行证据。
