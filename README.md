# claw-trade

## 系统原始数据

A 股出厂恢复包：

```text
data/a-share-cn-required-300td-20260608.tar
```

说明和校验文件见 [data/README.md](data/README.md)。
恢复脚本：`scripts/selection/restore_a_share_factory_seed.py`。
增量合并脚本：`scripts/selection/merge_a_share_factory_seed_incremental.py`。

## 专业投研报告数据源配置

真正专业报告：

```dotenv
POLYGON_API_KEY=付费
FINNHUB_API_KEY=付费 或 FMP_API_KEY=付费
FRED_API_KEY=免费
SEC_USER_AGENT=免费
```

## Research UI 启动

日常启动（默认不重新编译前端，直接使用 `web/research-ui/dist`）：

```bash
scripts/start-research-ui.sh
```

如果你改了前端代码，需要先手动重新编译：

```bash
pnpm --dir web/research-ui build
# 或
cd web/research-ui && pnpm build
```

编译完成后再启动：

```bash
scripts/start-research-ui.sh
```

如果你就是希望启动脚本先编译，也可以显式开启：

```bash
scripts/start-research-ui.sh --build
# 或
RESEARCH_UI_BUILD_FRONTEND=1 scripts/start-research-ui.sh
```
