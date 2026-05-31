# OpenBB Data Gateway TODO

## Data Layer Code-Doc Consistency Follow-Up

- [ ] T9B Crypto 历史包暂不做；不得保留 Binance 下载脚本、crypto history importer、hard-coded completed symbols 或成功测试。
- [ ] T9B 以后重启前，先单独批准 universe、source/exchange、history range、interval、license boundary。
- [x] 完成 T9A A 股历史 seed-to-Mongo 的同一批次验收：文件数量、universe、复权因子、批准范围内指数、AkShare 事件、Mongo readback、warehouse marker。11 个明确不下载指数、单个行业映射缺失、日线起止日期缺口不作为失败理由。
- [ ] 补齐 report/select 的细粒度 DataRequirement / SelectDataPlan 证据链，不能只用 domain pack 粗粒度计划冒充覆盖。
- [ ] 价格提醒和 UI probe 后续接统一数据入口；未接入前不能用默认 0 价格或 probe-only 结果当主链路成功。

## T0 Human Decision Gates

- [x] OpenBB repository URL: `https://github.com/OpenBB-finance/OpenBB`
- [x] Fixed version policy: use current OpenBB Platform release `v4.7.0`, commit `dddc3b328284bb953b6e5468b167b39c78490001`; do not pin floating `develop` or moving `ODP`.
- [x] License posture: personal research use approved for now; record OpenBB AGPL-3.0 risk and keep raw provider export conservative by default.
- [x] Extension placement: project provider/pack code lives under `src/claw_trade/data_gateway/**` and is loaded by OpenBB runtime as an extension/package; `third_party/openbb` may only contain a thin generic shim if needed.
- [x] Verify OpenBB `v4.7.0` can expose `get_market_pack`, `get_fundamental_pack`, `get_news_pack`, and `get_social_pack` or equivalent MCP/FastAPI endpoints. If not, stop and redesign instead of falling back to local Python direct provider calls.

## Declarative Provider Security

- [ ] Testing phase policy: do not relax SSRF checks for any user-declarative provider that can enter a live `/report`.
- [ ] To reduce testing failures, keep user-declarative providers disabled for live reports until admission validation is implemented.
- [ ] During early tests, use only approved system providers and fixed OpenBB extension endpoints.
- [ ] Positive declarative-provider tests must use approved public test domains, not localhost/private IP bypasses.
- [ ] Negative tests must prove localhost, loopback, private IP, link-local, metadata service, unapproved protocols, and redirect-to-private targets are rejected.

## D1/T5 Tool Exposure

- [ ] Expose a pack-only FastAPI app through OpenBB MCP.
- [ ] Keep OpenBB MCP discovery disabled for report runtime.
- [ ] Verify the model-visible tool list contains only approved pack endpoints.
- [ ] Do not rely on OpenBB category settings alone as the pack exposure boundary.

## OpenViking Runtime Health Follow-Up

- [ ] Split OpenViking health reporting into two reader-visible statuses: `/report` material/evidence chain health and optional OpenViking enhanced capability health.
- [ ] Keep current evidence wording honest: report chain can be OK while semantic/vector queue, metrics, or recovery remain unavailable.
- [ ] Decide whether to connect `/metrics` for local report runtime, or explicitly document it as unsupported in this runtime profile.
- [ ] Confirm whether OpenViking exposes a stable public recovery/redo API; if not, keep `recovery_status=unavailable` and do not substitute `/health`.
- [ ] Decide whether semantic/vector indexing is needed for claw-trade; if enabled later, prove it with live evidence and keep it control-plane only, not worker-visible or investment-fact authority.
- [ ] UI requirement: do not show `runtime_health.status=blocked` as a single scary global failure when `/report`, relations, ovpack, and evidence audit passed.

## Settings Advanced Diagnostics Follow-Up

- [ ] Add provider health to the advanced diagnostics view.
- [ ] Add runtime service status to the advanced diagnostics view.
- [ ] Add recent live run gap summary to the advanced diagnostics view.
- [ ] Add evidence-chain failure reason summary to the advanced diagnostics view.
- [ ] Keep these diagnostics out of the first-run user setup path unless they block report generation.

## Evidence Docs To Write Before D0

- [x] `docs/evidence/openbb-submodule-version.md`
- [x] `docs/evidence/openbb-declarative-provider-security.md`
