# OpenBB Data Gateway TODO

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

## Evidence Docs To Write Before D0

- [x] `docs/evidence/openbb-submodule-version.md`
- [x] `docs/evidence/openbb-declarative-provider-security.md`
