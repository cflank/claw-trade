# T-CL-1 口径冻结与当前偏差基线（2026-05-18）

状态：DONE（仅基线取证，不代表接入完成）  
任务：T-CL-1  
时间：2026-05-18

## 1) 范围与约束

- 只做口径冻结与现状取证。
- 未修改 `src/`、`agents/`、`openclaw_plugins/`、`scripts/`、`tests/` 等运行代码。
- 当前工作区有他人未提交改动，未回退、未覆盖。

## 2) 已确认事实

### A. CryptoLens 定位边界（不是数据源/provider/MCP/worker/决策器）

- `docs/CryptoLens接入方案.md:8`：CryptoLens 不是数据源，也不是 provider 入口。  
- `docs/CryptoLens接入方案.md:16`：CryptoLens 不是数据源、provider、外部 MCP、独立 worker、trader/PM 决策器。  
- `docs/CryptoLens实施任务清单.md:13`：重复确认同一定位。  
- `docs/evidence/cryptolens-orchestration-20260518.md:13`：总管清单重复确认同一定位。

### B. OpenBB 是唯一外部取数入口

- `docs/CryptoLens接入方案.md:18`：OpenBB 是唯一外部数据入口/唯一 provider 接口层/唯一数据 MCP/唯一 provider evidence 记录者。  
- `docs/CryptoLens接入方案.md:149`：OpenBB 是唯一外部数据入口。  
- `docs/数据源openbb引入方案.md:90`、`docs/数据源openbb引入方案.md:154`：OpenBB 是唯一外部数据入口。  
- `docs/CryptoLens实施任务清单.md:14`：重复确认同一口径。

### C. 当前 canonical BTC 证据仍是窄覆盖，不是 CryptoLens 覆盖完成

- `docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/final_report.md:49`：来源仅见 `openbb_yfinance/crypto_price_historical`。  
- `docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/final_report.md:223-224`：明确写明 BB/CoinGlass 衍生品维度、链上、宏观、AHR999 未覆盖。  
- `docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/final_report.md:230`：超过 60% 预设分析维度无数据可用。  
- `docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/worker-appendix/01-market_analyst.md:11`：同样明确来源成功记录只有 `openbb_yfinance/crypto_price_historical`，其余关键维度未覆盖。  
- `rg -n "crypto_lens_analysis_evidence|crypto_lens|CryptoLens" docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc` 返回空（exit code 1）：当前 canonical BTC 目录未出现 CryptoLens 接入完成所需的分析 evidence 标识。

### D. 后续任务不得恢复旧 BB 直连

- `docs/CryptoLens接入方案.md:32`（禁止链路）与 `docs/CryptoLens接入方案.md:69`（修复目标不是恢复旧 BB 直连）。  
- `docs/CryptoLens实施任务清单.md:66`：明确后续任务不得恢复旧 BB 直连。  
- `docs/CryptoLens实施任务清单.md:378`：禁止旧 BB MCP、旧 BB 直连、CryptoLens 再调 OpenBB。

## 3) 结论（T-CL-1 判定）

- 当前状态可以证明：报告对“数据缺口”有诚实披露。  
- 当前状态不能证明：CryptoLens 已完成接入并覆盖 CRYPTO 关键维度。  
- 因此“CryptoLens 接入完成”判定在当前证据下为**不成立**。

## 4) T-CL-1 验收覆盖

- [x] 记录 CryptoLens 非数据源/provider/MCP/worker/决策器。  
- [x] 记录 OpenBB 是唯一外部取数入口。  
- [x] 记录 canonical BTC final report 仅证明诚实缺口，不证明 CryptoLens 覆盖完成。  
- [x] 明确当前 CRYPTO market source 仍是 `openbb_yfinance/crypto_price_historical` 的窄覆盖。  
- [x] 明确后续任务不得恢复旧 BB 直连。

## 5) 本次执行命令（摘录）

1. `sed -n '1,260p' AGENTS.md`（exit 0）  
2. `sed -n '1,260p' docs/CryptoLens接入方案.md`（exit 0）  
3. `sed -n '1,260p' docs/CryptoLens实施任务清单.md`（exit 0）  
4. `sed -n '1,260p' docs/数据源openbb引入方案.md`（exit 0）  
5. `sed -n '1,260p' docs/evidence/cryptolens-orchestration-20260518.md`（exit 0）  
6. `rg -n "openbb_yfinance|crypto_price_historical|资金费率|OI|清算|链上|AHR999|宏观" docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/worker-appendix/01-market_analyst.md docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/exported/final_report.md`（exit 0）  
7. `rg -n "crypto_lens_analysis_evidence|crypto_lens|CryptoLens" docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc`（exit 1，空输出）  
8. `git status --short`（exit 0，确认工作区已有他人改动）

## 6) 偏差与风险备注

- 当前 report 文本中仍多处建议“补充 BB/CoinGlass 接口”；这只能作为历史缺口描述，不能被解释为允许恢复旧 BB 直连 runtime。
- 后续实现必须以 OpenBB provider contract + CryptoLens 离线分析层为唯一路径，且保持“不出网、不读 key、不冒充 provider evidence”。
