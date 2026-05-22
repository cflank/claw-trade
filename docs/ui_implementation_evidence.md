# UI 实施证据（I-FOUNDATION）

更新时间：2026-05-19  
基线提交：`a4b31e1`

## P1-00 实施前源文档校验

已读源文档：

1. `AGENTS.md`
2. `docs/UI实施任务清单.md`
3. `docs/UI需求分析.md`
4. `docs/UI详细设计.md`
5. `docs/report_workflow_env_and_rounds_design.md`

只读核验命令入口：

```bash
rg -n "UI实施任务清单|UI详细设计|RunRequest" docs memory
```

## P1-01 首版范围闸门（本 subagent 范围）

已落地入口：

- `src/claw_trade/ui_contracts/scope_guard.py`
- `tests/unit/ui/test_no_first_version_regression.py`

覆盖的首版不做项：

- NT-01 移动端首版布局
- NT-02 并行完整报告
- NT-03 小时级完整报告
- NT-04 简报 worker
- NT-06 失败任务管理页
- NT-07 未知 HTTP/JSON 数据源
- NT-08 claw-trade 微信协议实现

## P1-03/P1-07/P1-10 交付入口

- 传输无关 API contract：`src/claw_trade/ui_contracts/api_contracts.py`
- DTO 与 mapper：`src/claw_trade/ui_contracts/user_dto.py`
- 字段边界检查：`src/claw_trade/ui_contracts/api_contracts.py`
- 公共错误翻译：`src/claw_trade/ui_backend/error_translator.py`

## AR-01 到 AR-06 当前结论

范围：本结论只覆盖 I-FOUNDATION/I-FOUNDATION-FIX 已修改的公共 contract、DTO、错误翻译器、证据文档和对应测试。聊天、队列、报告、设置、PDF、Channel 业务模块尚未由本 subagent 实现，因此不在这里判定通过。

实际执行入口：

```bash
rg -n "TODO|stub|mock|fake|fallback|pass$|NotImplemented" src tests web docs/ui_implementation_evidence.md docs/ui_blocker_register.md
```

若 `web/` 不存在，替代范围：

```bash
rg -n "TODO|stub|mock|fake|fallback|pass$|NotImplemented" src tests docs/ui_implementation_evidence.md docs/ui_blocker_register.md
```

当前结论：

- AR-01 mock/stub/fake/fallback：foundation 范围未用这些路径证明 OpenClaw、Channel、PDF 文件发送、微信入站回调、LLM 保存或数据源测试成功。证据：`tests/contracts/test_ui_api_contracts.py` 只验证传输无关 contract 和红线；`docs/ui_blocker_register.md` 将 B-02/B-03 保持为待探测。
- AR-02 骨架空实现：foundation 范围函数均有真实行为：DTO mapper 做字段转换，`validate_ui_api_response` 做禁止字段检查，`translate_internal_error_for_user` 做产品错误码映射，`scope_guard` 会实际拒绝首版不做项。证据：`tests/unit/ui/test_no_first_version_regression.py`、`tests/unit/ui/test_error_message_translator.py`、`tests/contracts/test_ui_user_dto_redaction.py`、`tests/contracts/test_ui_api_contracts.py`。
- AR-03 内部概念泄露：foundation 范围对普通用户 payload 执行 DTO 和禁止字段检查；DTO 测试覆盖 `runId`、`lockedWorkflowRunId`、`dedupeKey`、`credentialRef`、`providerChannelId`、内部路径/证据类字段不进入用户 payload。证据：`tests/contracts/test_ui_user_dto_redaction.py`。
- AR-04 未知 API 被当成已存在：foundation 范围没有实现 Channel、PDF、LLM bridge 的成功路径；B-01 已记录映射事实，B-04 已记录 config/models 入口事实，B-02/B-03 仍是运行时探测 blocker。证据：`docs/ui_blocker_register.md`。
- AR-05 首版不做项被引入：foundation 范围用 `scope_guard` 固定拒绝移动端首版、并行完整报告、小时级完整报告、简报 worker、失败任务管理页、任意未知 HTTP/JSON 数据源、claw-trade 微信协议实现。证据：`tests/unit/ui/test_no_first_version_regression.py`。
- AR-06 文档未授权产品/架构决策：foundation 范围未新增产品能力；DTO 字段按 `docs/UI详细设计.md` §7 和 `docs/UI实施任务清单.md` §2 输出，错误码按 §2.4 输出，blocker 按 §10 表达。未覆盖的业务模块不在本结论中判定通过。
