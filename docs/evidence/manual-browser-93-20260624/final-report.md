# 93-人工浏览器集成测试用例 最终汇总

测试日期：2026-06-24  
执行人：Codex  
浏览器路径：按 memory 固定路径使用 `playwright-win-chrome` MCP，实际 CDP 端点 `http://127.0.0.1:9333`  
Chrome 版本：Chrome/146.0.7680.165  
访问地址：`http://172.27.36.34:5175/`  
环境类型：真实 runtime/UI 环境，OpenViking 1933、OpenClaw 18789、UI 5175 健康  
代码修改：无  
证据目录：`docs/evidence/manual-browser-93-20260624/`

## Preflight

固定 runtime preflight 已执行并保存：`preflight.txt`。

- OpenViking：200
- OpenClaw：200
- UI：200
- 微信渠道：`wechat_clawbot`，`state=connected`，`canSendText=true`，`canSendFile=true`
- 运行配置：`.runtime/dev-services/openviking/ov.conf`、`.runtime/dev-services/openviking/data`

证据限制：

- MCP `browser_take_screenshot` 在全页和视口截图上均因 5 秒截图超时失败；本报告主要引用 Chrome accessibility snapshot、页面文本、网络记录和 HTTP 状态补充。
- 未修改代码，未触发真实渠道发送，未删除历史报告，未确认启动新的长链路正式报告。

## 总结

本轮覆盖了 `93-人工浏览器集成测试用例.md` 的全部 26 个用例编号，并逐项给出当前 Chrome 验收状态。结论不是“全通过”。

- 通过：TC-01、TC-02、TC-13、TC-20
- 部分通过：TC-04A、TC-07、TC-08A、TC-09、TC-10、TC-12A、TC-17、TC-18、TC-19、TC-21
- 未通过或阻塞：TC-03、TC-03A、TC-04、TC-04B、TC-05、TC-06、TC-08、TC-11、TC-12、TC-12B、TC-14、TC-14A、TC-15、TC-15A、TC-16、TC-19A

主要问题分组：

1. selection 链路未达预期：`/select` 运行后失败；`/select US`、`/select 3` 返回普通“回复失败”，不是明确“不支持”；`/select CRYPTO` 触发补数，但没有清楚展示 CRYPTO 候选语义。
2. 正式报告运行证据入口缺失：报告详情未看到 worker 附录、provider payload、tool schema、approved material、receipt、manifest 的 Chrome 可见入口或下载入口。
3. 后台业务入口缺失：未看到定时报表、价格提醒、维护状态的普通用户可见入口。
4. 四市场报告解析不完整：A 股和 CRYPTO 能展示较完整确认信息；US/HK 名称显示“名称未查到”；HK 未 fail-closed，而是给确认卡。
5. 渠道发送、外部入站、删除报告属于真实外部/破坏性操作，本轮无人值守未执行成功路径，只验证可见状态。

## 逐项结果

| 用例 | 状态 | 结论和证据 |
| --- | --- | --- |
| TC-01 普通聊天不触发报告 | 通过 | 普通问题和“帮我看看 AAPL”均为普通消息；任务状态 `0 运行中 · 0 排队中`。证据：`home.snapshot.json` |
| TC-02 `/report` 输入校验和确认 | 通过 | `/report` 提示“请输入完整的 /report 指令”；`/report 600519.SH` 出确认卡，标的 `600519.SH`、名称 `贵州茅台`，未确认前队列仍为 0。证据：`tc02-report-missing.snapshot.json`、`tc02-report-600519.snapshot.json` |
| TC-03 `/select` 候选发现和刷新入口 | 未通过 | `/select` 先进入选股中，等待后变为“选股不可用”；`/select US`、`/select 3` 是“回复失败”，不是明确不支持；`/select CRYPTO` 触发补数批次 `sel-run-d2017994182941cba06fe09ffc4044d0`，未展示候选。证据：`tc03-select-*.snapshot.json` |
| TC-03A `/select` 命令矩阵和 worker 证据 | 未通过 | 仅看到 selection 四阶段文案：策略评审、反方评审、整合排序、组合经理；没有候选 cache hash/readback、worker provider payload、工具可见性证据入口。证据：`tc03-select-default-running.snapshot.json` |
| TC-04 `/report` 创建、队列和运行中状态 | 阻塞 | 本轮只验证到确认卡，没有点击确认启动新正式报告；因此未产生新 task id/run id。证据：`tc02-report-600519.snapshot.json` |
| TC-04A 四市场 `/report` 最小 Chrome 验收 | 部分通过 | A 股：`600519.SH/贵州茅台`；US：`AAPL/名称未查到`；HK：`00700.HK/名称未查到` 且未 fail-closed；CRYPTO：`BTC/USDT/Bitcoin`。证据：`tc02-report-600519.snapshot.json`、`tc04a-report-*.snapshot.json` |
| TC-04B 正式报告运行证据核查 | 未通过 | 历史报告可打开，但详情页没有 provider payload、tool schema、material/receipt/manifest、worker 附录下载或展示入口。证据：`tc07-report-detail-open.snapshot.json` |
| TC-05 报告任务取消 | 阻塞 | 未启动新的 queued/running 报告任务；只取消了确认卡，不能等同任务取消。 |
| TC-06 报告失败状态和错误文案 | 阻塞 | 高级诊断显示 provider 健康异常，但本轮未创建 `/report AAPL` 失败任务，不能验收任务失败详情。证据：`tc19a-tc21-diagnostics.snapshot.json` |
| TC-07 完整报告完成和历史入库 | 部分通过 | 历史列表已有 8 份报告，可打开最新 AAPL 详情；不是本轮新建完成报告。证据：`home.snapshot.json`、`tc07-report-detail-open.snapshot.json` |
| TC-08 报告详情正文、worker 附录和 PM 结论 | 未通过 | 正文和 PM 结论可见，图表/PDF状态可见；worker 附录或阶段详情入口缺失，按 93 文档不能算通过。证据：`tc07-report-detail-open.snapshot.json` |
| TC-08A 独立报告问答不改写原报告 | 部分通过 | 报告页 worker 聊天能用当前报告上下文回答问题，原正文仍在页面；但页面入口标识为“报告 worker 聊天”，不是独立“报告问答”入口。证据：`tc13-worker-chat.snapshot.json` |
| TC-09 图表证据状态 | 部分通过 | 报告正文有 `market-chart-1/2` 图片节点，侧栏显示两个图表 `ready`；未能保存 PNG 截图，未逐图点击展开。证据：`tc07-report-detail-open.snapshot.json` |
| TC-10 PDF 导出 | 部分通过 | 侧栏显示 PDF `ready` 和“保存为 PDF”按钮；未点击 Chrome 打印或后端下载，未生成 PDF 文件。证据：`tc07-report-detail-open.snapshot.json` |
| TC-11 selection 确认后交接报告 | 阻塞 | selection 未产生候选结果，因此无法确认候选生成报告。证据：`tc03-select-*.snapshot.json` |
| TC-12 渠道状态和报告文件发送 | 阻塞 | 微信状态已连接，但本轮未获“测试渠道不会发到非测试渠道”的显式确认，未点击真实转发/发送。证据：`preflight.txt`、`home.snapshot.json` |
| TC-12A 渠道配置保存和 probe | 部分通过 | 设置页通用 tab 显示微信已连接、重新连接/解除连接/刷新二维码入口；未修改配置、未 probe。证据：`tc12a-settings-general-channel.snapshot.json` |
| TC-12B 入站渠道消息 | 阻塞 | 没有外部测试渠道客户端消息输入证据，未执行入站普通消息或入站 `/report`。 |
| TC-13 worker 聊天 | 通过 | 报告 worker 聊天向组合经理提问后收到上下文回答，未创建新报告任务。证据：`tc13-worker-chat.snapshot.json` |
| TC-14 定时报表管理 | 未通过 | 首页、设置页、高级诊断页未见定时报表管理入口。证据：`home.snapshot.json`、`tc12a-settings-general-channel.snapshot.json`、`tc19a-tc21-diagnostics.snapshot.json` |
| TC-14A 定时报表 cron wake 和幂等 | 未通过 | 未见定时报表/cron/wake 可见入口，不能验证 cron job id、wake、幂等。 |
| TC-15 价格提醒管理和立即检查 | 未通过 | 首页、设置页、高级诊断页未见价格提醒入口。 |
| TC-15A 价格提醒触发、扫描和去重 | 未通过 | 未见价格提醒和扫描证据入口，不能验证报价、触发、不触发、dedupe、bucket。 |
| TC-16 系统后台任务和维护状态 | 未通过 | 高级诊断仅显示 provider 健康、运行服务、最近 live run、证据链摘要；未见 selection 数据刷新、数据维护、报告清理维护状态入口。证据：`tc19a-tc21-diagnostics.snapshot.json` |
| TC-17 报告清理保护 | 部分通过 | 历史报告列表有“删除”按钮；无人值守下未执行删除确认和保护态验证。证据：`home.snapshot.json` |
| TC-18 设置和模型设置 | 部分通过 | 模型、Embedding 配置可见，含服务商、模型、base URL、API Key 占位、测试/保存按钮；未实际修改、保存、取消、重置。证据：`tc18-tc19-settings.snapshot.json` |
| TC-19 数据源设置和诊断 | 部分通过 | 数据源 tab 显示 7 个 API 源、启用状态、编辑、测试、保存；高级诊断显示 provider 健康异常和中文建议；未执行连接测试。证据：`tc19-settings-data-sources.snapshot.json`、`tc19a-tc21-diagnostics.snapshot.json` |
| TC-19A 数据源保存和数据网关证据 | 未通过 | 可见数据源保存 UI，但没有 provider attempts、raw/normalized/metadata/columnar/manifest、data gap/chart evidence 的 Chrome 可见入口或下载入口。证据：`tc19-settings-data-sources.snapshot.json` |
| TC-20 API 404 和 SPA fallback | 通过 | 前端路由回到应用并提示未找到报告；`/api/ui/not-real-api` 和 `/api/other` 为 404 接口错误；静态资源为 404 Not Found。证据：`tc20-*.snapshot.json`、`tc20-*.curl.txt` |
| TC-21 权限、配置缺失和运行中状态总检 | 部分通过 | 首页空/普通状态、selection 运行中/失败、设置可用、诊断 provider 异常可见；定时报表/价格提醒/维护入口缺失，不能完整通过。证据：`home.snapshot.json`、`tc03-select-*.snapshot.json`、`tc19a-tc21-diagnostics.snapshot.json` |

## Collect-first Compliance

- batch scope：`docs/项目文档/93-人工浏览器集成测试用例.md` 全部 26 个用例编号
- completed items：全部用例均完成当前 Chrome 可见状态判定
- failures collected：selection、正式报告运行证据、worker 附录、定时报表、价格提醒、维护状态、渠道外部动作、数据网关证据入口
- early-stop exception used：no
- batch fix grouping：不修改代码；本报告仅给出缺陷/阻塞分组

## 结论

当前 UI 已能支撑普通聊天、`/report` 确认卡、历史报告阅读、报告 worker 聊天、设置页、诊断页和 API/SPAfallback 边界的基础验收。

但按 93 文档标准，完整人工浏览器集成测试没有全通过。核心缺口是：selection 成功路径不可用、报告运行证据不可从 Chrome 追踪、worker 附录缺失、定时报表/价格提醒/维护状态入口缺失，以及真实渠道/删除/PDF 下载等成功路径未在无人值守条件下执行。
