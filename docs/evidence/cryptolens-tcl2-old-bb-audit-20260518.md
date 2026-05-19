# T-CL-2 旧 BB 只读盘点与迁入/剔除清单（2026-05-18）

状态：DONE_WITH_CONCERNS（旧 BB 可读；旧 BB 本地 `.env` 增量文件未发现；已补 `.env.local` 旧 BB runtime/MCP 变量名盘点与清理）  
任务：T-CL-2  
时间：2026-05-18

## 1) 范围与约束

- 只读盘点来源：`/mnt/d/src/BB/**`（未执行旧 BB 代码）。
- 未将旧 BB 目录接入当前仓库 runtime。
- 未复制任何 secret；只记录变量名、用途、归属和迁移决策。
- 本次仅产出证据文档，不改 `src/` 运行代码。

## 2) 只读可访问性与阻塞检查

- `/mnt/d/src/BB` 可访问（`ls -la /mnt/d/src/BB` exit 0）。
- 旧 BB `.env` 本地增量文件未发现：仅存在 `/mnt/d/src/BB/.env.example`。  
  因此“旧 BB `.env`（含本地增量）差异盘点”中，“本地增量部分”记为**证据缺口**，不伪造。

## 3) 可迁入清单（仅纯分析/纯计算）

目标落点统一映射到：`src/claw_trade/data_gateway/analysis/crypto_lens/**`（当前目录尚不存在，T-CL-6 创建）。

| 旧 BB 来源（只读） | 纯分析能力 | 迁入目标文件（建议） | 结论 |
|---|---|---|---|
| `mcp/crypto-data-mcp/src/domains/technical/indicators.ts` | RSI/MACD/KD/Bollinger/ATR/EMA/SMA/TD Sequential | `engine/technical.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/vegas.ts` | Vegas 双通道（144/169/576/676） | `engine/vegas.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/fvg.ts` | FVG 候选与填补状态 | `engine/patterns_fvg.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/tutorialPatterns.ts` | OB、双线反转、谐波、成交量分布 | `engine/patterns_structural.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/rule123.ts` | 123 突破结构 | `engine/patterns_rule123.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/amd.ts` | AMD/SMC 阶段识别与联动信号 | `engine/patterns_amd.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/rejection.ts` | 关键位 rejection 信号 | `engine/patterns_rejection.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/levels.ts` | pivot/support/resistance | `engine/levels.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/ohlcv.ts` | OHLCV 清洗、样本状态与限制生成 | `input_contract.py` + `engine/normalization.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/technical/summary.ts` | 多周期信号汇总与冲突表达 | `engine/summary.py` | 迁入 |
| `mcp/crypto-data-mcp/src/domains/live.ts:309-647` | 多周期结构、相邻高周期对齐、AMD 对齐降级 | `engine/multi_timeframe.py` | 迁入（仅纯计算段） |
| `mcp/crypto-data-mcp/src/domains/live.ts:1970-2239` | 清算热力聚类归一化、衍生品拥挤度/CVD 聚合解释 | `engine/derivatives.py` + `engine/liquidation.py` | 迁入（仅纯计算段） |
| `mcp/crypto-data-mcp/src/domains/live.ts:2553-3085` | onchain/macro/events 风险解释、信号治理、plain-language 解释口径 | `engine/onchain_macro.py` | 迁入（仅纯计算段） |
| `mcp/crypto-data-mcp/src/domains/live.ts:1305-1365,3211-3224` | AHR999 计算与拟合价格（本地公式部分） | `engine/ahr999.py` | 迁入（仅纯计算段） |
| `mcp/crypto-data-mcp/src/core/readiness.ts` | readiness 评分与等级 | `engine/readiness.py` | 迁入 |
| `mcp/crypto-data-mcp/src/core/conflicts.ts` | 冲突值解析（facts conflict） | `engine/conflicts.py` | 迁入 |
| `mcp/crypto-data-mcp/src/core/types.ts`（分析相关结构） | 非 provider 输出结构（signals/gaps/conflicts） | `output_contract.py` | 迁入（重定义为 Python 契约） |

补充：`readiness/data_gaps/conflicts` 的纯计算位点还包括 `mcp/crypto-data-mcp/src/domains/live.ts:508-543,713-836`，可拆到 `engine/readiness.py`。

## 4) 剔除清单（禁止进入 report runtime）

### A. 直接 HTTP / provider 直连（必须剔除）

- `mcp/crypto-data-mcp/src/domains/live.ts:17-30`：硬编码外部 provider base URL（CoinGecko/CoinGlass/Binance/Bybit/FRED/Glassnode/DefiLlama/Tavily/Snapshot 等）。
- `mcp/crypto-data-mcp/src/domains/live.ts:91-117,927-1303,1373-1790`：各域直接 `fetchJson/fetch` 访问外部 provider。
- `mcp/crypto-data-mcp/src/providers/binanceKlines.ts:42-118`：Binance futures/spot 直连。

### B. API key 读取（必须剔除）

- `mcp/crypto-data-mcp/src/domains/live.ts:80-84,1330,1367-1370,1766,1900-1919`：读取 `process.env.*` 决定 provider 访问与 header 注入。

### C. provider cache / rate-limit / retry（必须剔除）

- `mcp/crypto-data-mcp/src/domains/live.ts:1394-1414`：provider promise cache 与缓存删除。
- `mcp/crypto-data-mcp/src/providers/http.ts:31-103`：provider context cache、Coinglass budget、rate-limit window。
- `mcp/crypto-data-mcp/src/providers/http.ts:52-72`：provider retry/backoff/timeout。

### D. fallback 取数路径（必须剔除）

- `mcp/crypto-data-mcp/src/domains/live.ts:162-171`：Binance 失败后 fallback 到 CoinGecko OHLC。
- `mcp/crypto-data-mcp/src/domains/live.ts:1330-1341`：AHR999 Coinglass 失败后 fallback 到 CoinGecko。
- `mcp/crypto-data-mcp/src/domains/live.ts:81-84,847-853`：Coinglass 与 public derivatives 混合路径。

### E. MCP server/runtime（必须剔除）

- `mcp/crypto-data-mcp/src/server.ts`、`src/httpServer.ts`、`src/cli.ts`。
- `scripts/start-bb-crypto-data-mcp.ps1`（旧 MCP 启动脚本）。
- **明确禁止**：`/mnt/d/src/BB/mcp/crypto-data-mcp/src/domains/live.ts` 及其依赖链，属于 live provider fetch 路径，**不得进入 claw-trade report runtime**。

## 5) 旧 BB `.env` 与当前 `.env.example` 差异（脱敏）

### 5.1 旧 BB 可见基线

来源：`/mnt/d/src/BB/.env.example`（仅此文件；未发现 `.env` 或 `.env.local`）。

### 5.2 变量分类与决策

| 变量名 | 用途 | provider/归属 | 决策 |
|---|---|---|---|
| `COINGECKO_PRO_API_KEY` / `COINGECKO_DEMO_API_KEY` | CoinGecko 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `COINGLASS_API_KEY` | CoinGlass 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `GLASSNODE_API_KEY` | Glassnode 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `FRED_API_KEY` | FRED 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `DEFILLAMA_API_KEY` | DefiLlama 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `TAVILY_API_KEY` | 搜索 provider 鉴权 | OpenBB/provider | 迁入（由 OpenBB 使用） |
| `CMC_API_KEY` / `CRYPTOQUANT_API_KEY` / `ETHERSCAN_API_KEY` / `THEGRAPH_ACCESS_TOKEN` | 其他外部源鉴权 | OpenBB/provider | 迁入（仅 provider 层；非 CryptoLens） |
| `BB_MCP_PORT` / `BB_MCP_CACHE_TTL_SECONDS` / `BB_MCP_LOG_LEVEL` | 旧 BB MCP runtime 配置 | 旧 BB MCP server | **不迁入 report runtime** |

### 5.3 与当前仓库 `.env.example` 对比结论

- 共有关键 key 已在当前仓库存在：`COINGECKO_* / COINGLASS_* / GLASSNODE_API_KEY / FRED_API_KEY / DEFILLAMA_API_KEY / TAVILY_API_KEY / CMC_API_KEY / CRYPTOQUANT_API_KEY / ETHERSCAN_API_KEY / THEGRAPH_ACCESS_TOKEN`。
- 当前仓库新增且保留（不来自旧 BB `.env.example`）：
  - OpenBB/crypto 运行配置：`COINGLASS_API_BASE`、`COINGLASS_API_HEADER_NAME`、`CRYPTO_PROVIDER_CACHE_*`、`CRYPTO_CACHE_TTL_*`、`CRYPTO_RATE_LIMIT_*`。
  - 旧 BB 兼容变量（仅过渡）：`BB_PROVIDER_*`、`BB_COINGLASS_*`、`BB_MCP_HTTP_*`、`BB_MCP_SERVER_PATH`、`BB_MCP_CWD`。  
  这些变量即使保留在配置层，也**不能**让旧 BB runtime 成为 report 依赖。
- 旧 BB 本地 `.env` 增量：未发现文件，无法做逐项差异；本项记为证据缺口。

## 6) 迁入边界判定（给 T-CL-6 的直接输入）

- 允许迁入：`technical + multi-timeframe + derivatives/liquidation/onchain/macro/AHR999` 的纯计算函数与输出契约。
- 禁止迁入：任何 `fetch/http/env key/cache/rate-limit/retry/fallback/mcp runtime` 代码路径。
- 落地要求：CryptoLens 只消费 OpenBB normalized bundle；不出网，不读 key，不访问 Mongo，不调用旧 BB MCP。

## 7) T-CL-2 验收覆盖

- [x] 可迁入清单覆盖：technical analyzers、多周期、AMD/SMC、FVG、OB、123、Vegas、双线反转、谐波、成交量分布、清算解释、derivatives 拥挤度、onchain/macro/AHR999、readiness/data_gaps/conflicts、非 provider 输出结构。  
- [x] 剔除清单覆盖：直接 HTTP fetch、API key、provider cache、provider rate limit、各外部源直连、MCP server runtime。  
- [x] 给出目标文件映射：`src/claw_trade/data_gateway/analysis/crypto_lens/**`。  
- [x] 旧 BB `.env.example` 与当前 `.env.example` 差异盘点（脱敏）。  
- [x] 明确 `.../src/domains/live.ts` live provider fetch 路径不得进入 report runtime。

## 8) 本次命令摘录（只读）

1. `ls -la /mnt/d/src/BB`（exit 0）  
2. `find /mnt/d/src/BB -maxdepth 4 -type d`（exit 0）  
3. `rg --files /mnt/d/src/BB`（exit 0）  
4. `rg -n "fetch\\(|API_KEY|rate.?limit|cache|COINGLASS|BINANCE|BYBIT|FRED|COINGECKO|GLASSNODE|DEFILLAMA|TAVILY|SNAPSHOT" /mnt/d/src/BB -g '!**/node_modules/**'`（exit 0）  
5. `nl -ba /mnt/d/src/BB/mcp/crypto-data-mcp/src/domains/live.ts | sed -n '1,3320p'`（分段执行，exit 0）  
6. `nl -ba /mnt/d/src/BB/mcp/crypto-data-mcp/src/domains/technical/*.ts`（分文件执行，exit 0）  
7. `nl -ba /mnt/d/src/BB/.env.example`（exit 0）  
8. `ls -la /mnt/d/src/BB/.env*`（exit 0，仅 `.env.example`）  
9. `nl -ba .env.example`（exit 0）

## 9) 偏差/风险

- 旧 BB `live.ts` 中存在“纯计算函数与 provider 直连函数同文件混排”现象；T-CL-6 必须先拆分再迁入，不能整文件复制。  
- 旧 BB 本地 `.env` 增量未提供，当前仅能基于 `.env.example` 和源码 `process.env.*` 位点做脱敏盘点。

## 10) `.env.local` 旧 BB 变量名盘点与决策（follow-up）

来源：当前仓库 `.env.local`（只读取变量名，不记录值）。

### 10.1 迁入 OpenBB provider key（保留）

- `COINGECKO_PRO_API_KEY`
- `COINGECKO_DEMO_API_KEY`
- `COINGLASS_API_KEY`
- `GLASSNODE_API_KEY`
- `FRED_API_KEY`
- `TAVILY_API_KEY`
- `EXA_API_KEY`
- `DEFILLAMA_API_KEY`
- `CMC_API_KEY`
- `CRYPTOQUANT_API_KEY`
- `ETHERSCAN_API_KEY`
- `THEGRAPH_ACCESS_TOKEN`

决策：保留，用于 OpenBB/provider 层，不作为旧 BB runtime 依赖。

### 10.2 旧 BB runtime/MCP 变量（删除）

- `BB_PROVIDER_TIMEOUT_MS`
- `BB_PROVIDER_RETRY_ATTEMPTS`
- `BB_PROVIDER_RETRY_DELAY_MS`
- `BB_COINGLASS_CONTEXT_BUDGET`
- `BB_COINGLASS_RATE_LIMIT_PER_MINUTE`
- `BB_COINGLASS_RATE_LIMIT_WINDOW_MS`
- `BB_MCP_HTTP_HOST`
- `BB_MCP_HTTP_PORT`
- `BB_MCP_HTTP_PATH`
- `BB_MCP_SERVER_PATH`
- `BB_MCP_CWD`

决策：从 `.env.local` 清理，不迁入目标态 report runtime。
