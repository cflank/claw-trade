# CN_A social 数据服务层部署配置与容量评审（T-DOC-001）

更新时间：2026-05-07  
对应任务：`T-DOC-001`  
对应章节：DLD §3.1、§3.2、§7、§7.1、§10

## 1. 范围与事实边界

本文件只覆盖 `social_analyst` 的 CN_A social 数据服务层部署与容量评审，不扩展到 news/fundamental 实现。

状态约定：

- `已确认`：代码或现有文档已可核对。
- `待确认`：尚无已批准生产结论，不能当作既定事实。

## 2. 部署配置清单（OpenClaw / OpenViking / MongoDB）

| 组件 | 端口/地址 | 认证方式 | 数据库/命名空间 | Secret 注入方式 | 状态 | 可核对来源 |
|---|---|---|---|---|---|---|
| OpenClaw Gateway | 本地默认 `ws://127.0.0.1:18789` | Gateway 凭据通过运行环境注入（具体生产凭据键未在本仓固定） | N/A | 进程环境变量；`scripts/start-control-runtime.sh` 会合并 `.env.local` 与 `~/.openclaw/.env` | 生产值待确认 | `scripts/start-control-runtime.sh` |
| OpenViking HTTP | 本地默认 `http://127.0.0.1:1933` | `X-API-Key`（`OPENVIKING_API_KEY`） | workspace 默认 `workflow` | 进程环境变量 / secret store | 生产值待确认 | `src/claw_trade/artifacts/openviking_backend_http.py`、`src/claw_trade/config/openviking_config.py` |
| OpenViking MCP | 本地默认 `127.0.0.1:1944` | 跟随 OpenViking 侧配置 | workspace `workflow` | 同上 | 生产值待确认 | `scripts/start-control-runtime.sh` |
| MongoDB（social cache） | 从 `CN_A_SOCIAL_MONGODB_URI` 解析（本地测试常见 `:27017`） | 由 URI 承载（用户名/密码/认证库） | DB 默认 `claw_trade`，collection 默认 `social_provider_cache` | 必须通过 secret/env 注入；代码不内置连接串 | 生产值待确认 | `agents/social_analyst/skills/cn-a-social-data/scripts/config.py`、`cache.py` |

补充说明（已确认）：

- social cache 只允许最小权限读写 `social_provider_cache`（DLD §7.1）。
- OpenViking L2 写入根路径由 `OPENVIKING_L2_WRITE_TARGET_ROOT` 注入。

## 3. Staging 部署前检查（验收必查）

### 3.1 MongoDB URI 配置来源

- 必须有 `CN_A_SOCIAL_MONGODB_URI` 的明确来源：部署系统 secret（如 K8s Secret / Vault / 平台环境变量）。
- `cache_required=true` 且缺 URI 时，启动检查应失败（阻断上线）。

### 3.2 Database 配置来源

- 必须有 `CN_A_SOCIAL_MONGODB_DATABASE` 配置来源说明（默认 `claw_trade` 仅可作为初始建议，不代表生产最终值）。
- 需在部署清单记录 staging/prod 的实际 DB 名称与变更单号。

### 3.3 Collection 权限配置来源

- 必须有 `CN_A_SOCIAL_MONGODB_CACHE_COLLECTION` 配置来源说明（默认 `social_provider_cache`）。
- 必须有 MongoDB 账号权限凭据来源：仅允许目标 DB 下该 collection 的最小读写权限；不得授予全库管理员权限。
- 验收前需能提供权限配置证据（角色定义/授权工单/ IaC 变更记录）。

## 4. 生产容量评审（含假设）

## 4.1 已确认容量参数

- 单个 social turn 触发 1 次 `social_social_sentiment_pack`。
- 每次 pack 默认 P0 endpoint 为 4 个；P1 `stock_hot_up_em` 依据开关启用；P1 雪球未启用。
- `CN_A_SOCIAL_PROVIDER_MAX_CONCURRENCY` 默认上限为 3。
- 单 endpoint timeout 默认 10s，整包 timeout 默认 20s。

## 4.2 评审假设（待负责人确认）

| 场景 | 每日 run 数（run/day） | 峰值并发 run 数 | provider 并发上限（每 run） | 理论 provider 峰值并发（run 并发 × 3） | 状态 |
|---|---:|---:|---:|---:|---|
| S1（保守） | 500 | 5 | 3 | 15 | 待确认 |
| S2（基线） | 2,000 | 20 | 3 | 60 | 待确认 |
| S3（高峰） | 5,000 | 50 | 3 | 150 | 待确认 |

说明：

- 若启用 `stock_hot_up_em`，日 provider 调用量估算为 `run/day × 5`；未启用时为 `run/day × 4`。
- 以上为容量评审输入，不是已批准生产指标。

## 4.3 运维告警与阈值（已确认实现）

高优先级告警：

- `SOCIAL_OPENVIKING_AUTH_FAILED`
- `SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH`
- `P0_ALL_FAILED`

中优先级告警：

- `SOCIAL_CACHE_WRITE_FAILED`
- `SOCIAL_PROVIDER_TIMEOUT`

运行检查关键项：

- MongoDB / OpenViking / provider 连续错误计数
- 最近一次成功时间
- 同次调用内 MongoDB 连续 3 次查询异常后进入 `SOCIAL_CACHE_INSPECTION_BYPASSED`

## 5. DLD §10 未定项处理顺序与决议记录

> 说明：以下“负责人”为角色归属，若尚未点名个人，统一标注“待指定”。

| 顺序 | 未定项 | 当前状态 | 负责人（角色） | 验收前置条件 |
|---:|---|---|---|---|
| 1 | OpenClaw/OpenViking/MongoDB 生产端口、编排与认证方式 | 待确认 | 待指定（平台运维负责人） | 发布正式部署清单；staging/prod 端口与认证字段可追溯到部署配置 |
| 2 | MongoDB 生产 URI / database / collection 权限与 secret 注入 | 待确认 | 待指定（DBA + 安全负责人） | 提供 secret 来源、最小权限授权证据、`cache_required=true` 启动检查通过 |
| 3 | QPS、日调用量、并发 run 指标 | 待确认 | 待指定（SRE/容量负责人） | 选定 S1/S2/S3 中的生产基线；超过单机预算时触发架构评审 |
| 4 | OpenViking L2 writer 运行时绑定与验收口径 | 设计已定、运行验收待完成 | 待指定（Runtime 负责人） | staging 上可验证 writer 绑定、receipt/hash 校验链路 |
| 5 | P1 雪球热度能力（endpoint 与字段映射） | 第一阶段禁用 | 待指定（数据源负责人） | HLD 补齐 endpoint 与字段映射并通过评审前，不得启用 `p1_xueqiu_enabled` |
| 6 | news summary 复用的自然语言 schema | 待确认 | 待指定（news+social 数据契约负责人） | 定义并批准自然语言 summary schema、字段信任边界与 guard 规则后方可复用 |
| 7 | 第一阶段是否强制 MongoDB | 已有方向（M1 可不强制，M2 起强制） | 待指定（架构负责人） | 发布阶段性决议并写入验收脚本；不得在 M2/M3 放宽 `cache_required` 目标 |
| 8 | social 情绪评分职责边界 | 待确认 | 待指定（Prompt/Guard 负责人） | 明确“数据层不出最终评分”的约束与 worker 报告 guard 文案 |

## 6. 当前实现边界（防误读）

- 当前仓库中 `pymongo` 使用仅在 social skill（`cn-a-social-data`）路径可核对。
- `P1` 雪球真实路径未启用，且配置层应继续阻断误开。
- 未发现可确认证据表明 news 或 fundamental 已进入 MongoDB 数据服务层生产路径；本文件不做此类宣称。

## 7. T-TST-002 集成测试运行说明

对应测试文件：`tests/integration/test_cn_a_social_pack_integration.py`

### 7.1 真实依赖前置

- 必须可访问真实 AkShare provider（用于 `600519` social pack 真实拉取）。
- 必须可连接真实 MongoDB（用于 `social_provider_cache` 读写）。
- 必须可访问真实 OpenViking HTTP 写入能力（用于 L2 raw/pack evidence）。

### 7.2 必填环境变量

- `CN_A_SOCIAL_MONGODB_URI`：真实 MongoDB 连接串。
- `OPENVIKING_ENDPOINT`：真实 OpenViking HTTP 地址。

可选环境变量（未设置使用默认值）：

- `CN_A_SOCIAL_MONGODB_DATABASE`（默认 `claw_trade`）
- `CN_A_SOCIAL_MONGODB_CACHE_COLLECTION`（默认 `social_provider_cache`）
- `OPENVIKING_API_KEY`（若 OpenViking 开启鉴权则必填）

### 7.3 测试覆盖点（与任务清单验收对齐）

- cache miss -> 真实 provider -> OpenViking L2 raw -> MongoDB upsert -> pack evidence。
- 新鲜 cache 再次执行同 query -> attempt `cache_status=hit` 且复用 raw ref/hash。
- OpenViking receipt hash mismatch 条件 -> 记录故障处理结果；该故障注入方式不作为继续实施的阻断 gate。
- 单 endpoint timeout 条件 -> 记录故障处理结果，其他 endpoint attempts 仍应被收集。

### 7.4 运行命令

```bash
pytest -q tests/integration/test_cn_a_social_pack_integration.py
```

说明：

- 若缺少必填真实依赖环境变量，测试会 `skip` 并在 skip 信息中明确缺失项。
- 测试不会启用 P1 雪球路径；配置固定 `p1_xueqiu_enabled=false`。
- receipt hash mismatch 与单 endpoint timeout 使用“真实依赖 + 受控故障注入”复现，按人工决议只记录为测试口径说明，不作为本阶段停止实施的 gate；最终通过与否以后续集中测试报告为准。
