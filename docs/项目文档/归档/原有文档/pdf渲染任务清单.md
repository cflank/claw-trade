# PDF 渲染与微信文件发送任务清单

本文把 `docs/PDF渲染方案.md` 拆成可交给 AFK agent 执行的垂直切片任务。目标不是按文件横向分工，而是让每个任务都能独立证明一段真实用户路径变好了。

## Verdict

任务拆解足够进入实施：先并行修依赖能力、Markdown 渲染副本清理和微信发送判定，再串行接入真实 PDF 主路径、兼容路径、artifact ready 门禁，最后做真实端到端验收。

## Task Graph

| ID | 任务名 | 类型 | 依赖 | 是否可并行 | 推荐 agent | 主要改动范围 | 验收命令/证据 |
|---|---|---|---|---|---|---|---|
| T1 | PDF 依赖与运行时能力门禁 | AFK | 无 | 是 | pdf-runtime-afk | `pyproject.toml`, `uv.lock`, `pdf_export_service.py` 或新 `pdf_renderer.py` | `uv run pytest tests/unit/ui/test_pdf_runtime_capabilities.py -q`; `pandoc --version`; `wkhtmltopdf --version`; `fc-match "Noto Sans CJK SC"` |
| T2 | Markdown 渲染副本清理器 | AFK | 无 | 是 | pdf-cleaner-afk | 新 `pdf_renderer.py` 或 `pdf_markdown_cleaner.py`, tests | cleaner 单测覆盖 YAML、表格、`---`、`...`、横排 HTML、标题兜底、hash 不变 |
| T3 | 中文 A4 HTML 渲染模板 | AFK | T2 | 部分 | pdf-html-afk | `pdf_renderer.py`, tests | HTML 单测断言 `zh-CN/ltr/UTF-8`、中文字体、A4、表格分页、图片 CSS、本地图片路径 |
| T4 | pdfkit + wkhtmltopdf 主路径与 PDF 校验门禁 | AFK | T1,T2,T3 | 否 | pdf-primary-afk | `pdf_export_service.py`, `pdf_renderer.py`, `pdf_validation.py`, repository tests | 真实 PDF `%PDF-`、页数 >= 1、可提取标题；失败不写 artifact、不 ready |
| T5 | pypandoc 兼容路径与引擎失败诊断 | AFK | T1,T2,T4 | 部分 | pdf-pandoc-afk | `pdf_renderer.py`, tests | 引擎顺序 `wkhtmltopdf -> weasyprint -> default`；全部失败记录 attempt；不生成假 PDF |
| T6 | 微信文件发送成功判定协议对齐 | AFK | 无 | 是 | wechat-send-afk | `channel_bridge.py`, tests | `messageId/message_id` 成功；`sent:false/ok:false/异常/空返回/非 mapping` 失败；文件发送无 `default_sent` 成功兜底 |
| T7 | 完整报告文件请求集成门禁 | AFK | T4,T6 | 否 | notification-afk | `report_notification_service.py`, `report_repository.py`, HTTP/e2e tests | PDF 未 ready 不发送；成功后不补发失败文案；缺文件/发送失败返回正确用户文案 |
| T8 | 真实端到端验收证据 | HITL | T1-T7 | 否 | live-e2e-hitl | 只允许补 live 测试/证据脚本；不改业务逻辑 | PDF 路径、`file`、`xxd`、`pdfplumber`、hash 不变、OpenClaw `messageId`、无失败补发 |

## Task Details

### T1. PDF 依赖与运行时能力门禁

- 依赖/并行/推荐 agent：无 / 是 / `pdf-runtime-afk`
- 目标：明确主路径、兼容路径、可选依赖，并在运行时缺关键能力时失败为 `failed`，不写 PDF artifact。
- 背景证据：`pyproject.toml` 当前无 `markdown/pdfkit/pypandoc/pdfplumber`；TradingAgents-CN app 主路径是 `markdown + pdfkit + wkhtmltopdf`；Dockerfile 安装 `pandoc/wkhtmltopdf/fonts-noto-cjk/fontconfig`；CN Docker 文档声称 WeasyPrint 优先但源码未落实。
- 改动范围：依赖文件、PDF capability 检查、聚焦单测。
- 不允许做什么：不把 WeasyPrint 写成当前 CN app 主路径；不把缺依赖当 warning 后继续 ready；不在 PDF 失败后产物 ready。
- 验收标准：主路径缺 `wkhtmltopdf` 或中文字体不可用时导出失败且 `pdf_artifact_id=None`；兼容路径调用前能识别 `pandoc`。
- 建议测试：新增 `tests/unit/ui/test_pdf_runtime_capabilities.py`。
- 完成后需要报告的证据：依赖分类、运行时检查输出、失败状态样例、测试命令和 exit code。

### T2. Markdown 渲染副本清理器

- 依赖/并行/推荐 agent：无 / 是 / `pdf-cleaner-afk`
- 目标：只清理送入 PDF 引擎的临时副本，正式 Markdown 不变。
- 背景证据：Web cleaner 保护 YAML/表格并处理 `---`、`...`、标题；app cleaner 处理 `writing-mode/text-orientation/style`，但 app PDF 主路径当前未调用 cleaner。
- 改动范围：新增 cleaner 类及测试。
- 不允许做什么：不回写 repository markdown；不自建另一套未验证规则；不破坏 Markdown 表格分隔符。
- 验收标准：清理副本覆盖开头 `---/...`、普通分隔线、省略号、表格、style 块、div/span style、标题兜底；导出前后原 Markdown hash 不变。
- 建议测试：`uv run pytest tests/unit/ui/test_pdf_markdown_cleaner.py -q`。
- 完成后需要报告的证据：每类输入/输出断言、hash 不变断言。

### T3. 中文 A4 HTML 渲染模板

- 依赖/并行/推荐 agent：T2 / 部分 / `pdf-html-afk`
- 目标：把 cleaned Markdown 渲染成适合 wkhtmltopdf 的中文横排 A4 HTML。
- 背景证据：TradingAgents-CN app `_markdown_to_html()` 使用 `tables/fenced_code/nl2br`，模板含 `zh-CN/ltr/UTF-8`、中文字体、A4、表格分页、图片样式。
- 改动范围：HTML renderer 与测试；必要时接入报告 `asset_dir` 作为本地图片 base。
- 不允许做什么：不声称本地图片支持是 CN 源码已有能力；不把远程图片加载失败伪装成功；不引入 WeasyPrint 主路径。
- 验收标准：HTML 包含方案要求 CSS；`assets/chart.png` 能解析为报告目录内资源；启用 `enable-local-file-access` 所需信息可传给 PDF 主路径。
- 建议测试：`uv run pytest tests/unit/ui/test_pdf_html_renderer.py -q`。
- 完成后需要报告的证据：HTML 片段断言、本地图片解析证据。

### T4. pdfkit + wkhtmltopdf 主路径与 PDF 校验门禁

- 依赖/并行/推荐 agent：T1,T2,T3 / 否 / `pdf-primary-afk`
- 目标：替换当前 `("PDF\n\n" + markdown)` 占位实现，走真实 pdfkit 主路径，校验后才写 artifact ready。
- 背景证据：`pdf_export_service.py` 当前不是 PDF；方案要求 `%PDF-`、可打开、页数 >= 1、可提取正文包含标题。
- 改动范围：`PdfExportService`、真实 renderer、validator、repository 写后确认。
- 不允许做什么：不生成假 PDF、空 PDF、改后缀 PDF；不让 validator 失败后写 artifact；不吞异常后 ready。
- 验收标准：真实导出的文件头 `%PDF-`；`pdfplumber` 或 `pypdfium2` 能打开；页数 >= 1；提取文本含标题；Markdown hash 不变；失败路径 record 为 `failed`。
- 建议测试：`uv run pytest tests/unit/ui/test_pdf_export_service.py tests/unit/ui/test_pdf_validation.py -q`，再跑一个真实导出命令。
- 完成后需要报告的证据：PDF 文件路径、`file`、`xxd` 前 8 字节、页数、提取文本关键词、hash 对比。

### T5. pypandoc 兼容路径与引擎失败诊断

- 依赖/并行/推荐 agent：T1,T2,T4 / 部分 / `pdf-pandoc-afk`
- 目标：实现旧 Web/Streamlit 风格兼容路径，作为主路径失败后的真实兼容渲染，不是伪成功。
- 背景证据：TradingAgents-CN web 使用 `pypandoc.convert_text(..., "pdf")`，引擎顺序 `wkhtmltopdf -> weasyprint -> pandoc default`。
- 改动范围：兼容 renderer、attempt 记录、失败诊断测试。
- 不允许做什么：不把 WeasyPrint 写成 app 当前主路径；不把 pandoc 缺失当成功；不丢失每个 engine 的异常类型、消息、输出文件状态。
- 验收标准：engine 顺序可测试；单个 engine 失败继续；全部失败返回 `failed` 且无 artifact；成功产物仍走 T4 validator。
- 建议测试：`uv run pytest tests/unit/ui/test_pdf_pandoc_fallback.py -q`。
- 完成后需要报告的证据：attempt 列表样例、失败诊断字段、真实/条件化 pandoc 检查结果。

### T6. 微信文件发送成功判定协议对齐

- 依赖/并行/推荐 agent：无 / 是 / `wechat-send-afk`
- 目标：文件发送按 OpenClaw 成功 payload 判定，`messageId/message_id` 即成功，但 `ok:false`、`sent:false` 优先失败。
- 背景证据：OpenClaw `send.ts` 构造 `runId/messageId/channel`；`deliver-types.ts` 要求 `messageId`；当前 `_to_send_result` 不把 `messageId` 当成功。
- 改动范围：`channel_bridge.py` 与对应单测。
- 不允许做什么：不修改 OpenClaw 源码，除非实测证明 gateway payload 本身错误并先停下来问人；文件发送不得使用 `default_sent` 兜底成功。
- 验收标准：覆盖 `{messageId}`、`{message_id}`、`{sent:true}` 成功；`{sent:false,messageId}`、`{ok:false}`、`{}`、`None`、`"ok"`、异常失败。
- 建议测试：`uv run pytest tests/unit/ui/test_channel_bridge.py -q`。
- 完成后需要报告的证据：判定矩阵、测试命令、关键断言。

### T7. 完整报告文件请求集成门禁

- 依赖/并行/推荐 agent：T4,T6 / 否 / `notification-afk`
- 目标：从“请求发送完整报告”到 channel bridge 的路径只发送 ready 且存在的 PDF，并正确显示成功/失败文案。
- 背景证据：方案要求 PDF 未 ready、artifact 路径不存在、OpenClaw 失败都触发“完整报告文件暂不可发送”；成功后不能再补发失败文案。
- 改动范围：`report_notification_service.py`、`report_repository.py`、HTTP/e2e 测试。
- 不允许做什么：不因为 PDF ready 就假设微信成功；不暴露本地路径；不在成功后补发“暂不可发送”。
- 验收标准：PDF failed 不调用文件发送；missing path/read 失败；channel 返回 messageId 成功；channel 返回空/非 mapping/false 失败。
- 建议测试：`uv run pytest tests/unit/ui/test_report_file_send.py tests/unit/ui/test_report_notification_service.py tests/e2e/ui/test_report_user_flows.py -q`。
- 完成后需要报告的证据：每个失败分支的返回码/文案、成功分支 messageId、是否调用 send 的 spy 证据。

### T8. 真实端到端验收证据

- 依赖/并行/推荐 agent：T1-T7 / 否 / `live-e2e-hitl`
- 目标：用真实 runtime 证明报告 Markdown -> 真 PDF -> 微信文件发送成功，不再补发失败文案。
- 背景证据：方案最终验收要求真实 PDF 文件证据和 OpenClaw `messageId` 证据；AGENTS 要求 live 前由主 agent 做 fixed runtime preflight。
- 改动范围：原则上只跑验收；如缺测试脚本，可新增窄 live test 或证据脚本。
- 不允许做什么：不 mock/stub/fake 微信发送；不跳过 `scripts/start-control-runtime.sh -- <command>`；不让 live subagent 自证 runtime ready。
- 验收标准：生成正式 Markdown；导出 PDF；`file` 显示 PDF；`xxd` 显示 `%PDF-`；pdfplumber 页数 >= 1 且含标题；Markdown hash 不变；OpenClaw 返回 `messageId`；通知服务无失败补发。
- 建议测试：主 agent preflight 后执行 `scripts/start-control-runtime.sh -- <focused live command>`，必要时 HITL 扫码。
- 完成后需要报告的证据：runtime preflight 表、PDF 路径、命令输出、OpenClaw payload、UI/通知日志片段。

## Agent Prompts

### T1 Prompt

```text
任务目标：实现 PDF 依赖与运行时能力门禁，缺主路径关键能力时 PDF export 必须 failed 且不写 artifact。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- pyproject.toml
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/report_repository.py
- /home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
- /home/frank/src/TradingAgents-CN/Dockerfile.backend
- /home/frank/src/TradingAgents-CN/docs/docker/pdf-export-support.md

允许修改范围：
- pyproject.toml, uv.lock
- src/claw_trade/ui_backend/pdf_export_service.py
- 可新增 src/claw_trade/ui_backend/pdf_renderer.py 或 pdf_validation.py
- tests/unit/ui/test_pdf_runtime_capabilities.py

禁止行为：
- 不允许假 PDF、空 PDF、改后缀 PDF。
- 不允许 PDF 失败后 artifact ready。
- 不允许把 WeasyPrint 写成当前 CN app 主路径。
- 不允许把 CN 文档声称但源码未落实的能力当事实。

验收标准：
- 主路径依赖 markdown/pdfkit 和 wkhtmltopdf/fontconfig/Noto CJK 能被检查。
- 兼容路径 pandoc/pypandoc 能被检查。
- 缺关键能力时 PdfExportRecord.state=failed, pdf_artifact_id=None。
- 测试通过：uv run pytest tests/unit/ui/test_pdf_runtime_capabilities.py -q。

最终汇报格式：
- 任务 ID
- changed files
- commands + exit code
- capability 分类：主路径/兼容路径/可选
- 失败时是否写 artifact：yes/no
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T2 Prompt

```text
任务目标：实现 TradingAgents-CN 来源的 Markdown 渲染副本清理器，只作用于 PDF 渲染副本，不回写正式 Markdown。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- /home/frank/src/TradingAgents-CN/web/utils/report_exporter.py
- /home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/report_repository.py

允许修改范围：
- 可新增 src/claw_trade/ui_backend/pdf_renderer.py 或 pdf_markdown_cleaner.py
- src/claw_trade/ui_backend/pdf_export_service.py 中仅允许接入 cleaner
- tests/unit/ui/test_pdf_markdown_cleaner.py
- tests/unit/ui/test_pdf_export_service.py 的 hash 相关断言

禁止行为：
- 不允许清理器回写正式 Markdown。
- 不允许破坏 Markdown 表格分隔符。
- 不允许声称 claw-trade 合并清理是 TradingAgents-CN app PDF 主路径现状。
- 不允许自建未验证清理规则替代方案文档规则。

验收标准：
- 覆盖开头 ---、开头 ...、普通 ---、普通 ...。
- 覆盖 |------| 和 |------|------| 表格保护。
- 覆盖 writing-mode、text-orientation、div/span style、style 块清理。
- 覆盖标题兜底和特殊引号归一化。
- 导出前后 repository markdown hash 不变。

最终汇报格式：
- 任务 ID
- changed files
- cleaner 规则覆盖表
- commands + exit code
- 原 Markdown 是否改变：yes/no
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T3 Prompt

```text
任务目标：实现中文 A4 横排 HTML renderer，支持表格、图片、代码块、引用，并为 wkhtmltopdf 提供本地图片访问条件。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- /home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
- src/claw_trade/ui_backend/report_repository.py
- src/claw_trade/ui_backend/pdf_export_service.py

允许修改范围：
- src/claw_trade/ui_backend/pdf_renderer.py 或 html renderer 新模块
- tests/unit/ui/test_pdf_html_renderer.py
- 必要时仅窄改 pdf_export_service 接入 report asset_dir

禁止行为：
- 不允许把远程图片加载失败当成功。
- 不允许把本地图片增强写成 CN 源码已有行为。
- 不允许引入 WeasyPrint 主路径。
- 不允许改写报告正文内容。

验收标准：
- HTML 含 html lang="zh-CN" dir="ltr" 和 meta UTF-8。
- CSS 含 Noto/Microsoft YaHei/SimHei、A4 20mm、横排、表格分页、图片 max-width、代码块换行。
- Markdown extensions 使用 tables/fenced_code/nl2br。
- 本地 assets/chart.png 能解析到报告资产目录或 base href/base_url 方案明确。

最终汇报格式：
- 任务 ID
- changed files
- HTML/CSS 断言清单
- 本地图片解析证据
- commands + exit code
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T4 Prompt

```text
任务目标：把 PDF 主路径改成真实 pdfkit + wkhtmltopdf，并在写 artifact 前校验 PDF 文件头、页数、可提取正文。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/report_repository.py
- /home/frank/src/TradingAgents-CN/app/utils/report_exporter.py

允许修改范围：
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/pdf_renderer.py
- src/claw_trade/ui_backend/pdf_validation.py
- src/claw_trade/ui_backend/report_repository.py 仅限写后确认所需窄改
- tests/unit/ui/test_pdf_export_service.py
- tests/unit/ui/test_pdf_validation.py

禁止行为：
- 不允许继续 return ("PDF\n\n" + markdown).encode("utf-8")。
- 不允许假 PDF、空 PDF、改后缀 PDF。
- 不允许 validator 失败后 ready。
- 不允许 PDF 失败后仍发送文件。

验收标准：
- pdfkit.from_string(html, False, options=...) 为主路径。
- options 包含 UTF-8、enable-local-file-access、A4、20mm margins。
- PDF bytes 必须 startswith b"%PDF-" 且达到最低大小。
- pdfplumber 或 pypdfium2 能打开，页数 >= 1，提取文本含标题/关键词。
- ReportRepository.write_pdf_artifact 只在校验通过后调用。
- 写后确认路径存在、header 正确、大小等于 bytes 长度。

最终汇报格式：
- 任务 ID
- changed files
- commands + exit code
- PDF 文件路径
- file/xxd/pdf page/text evidence
- Markdown hash before/after
- failed path 是否无 artifact
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T5 Prompt

```text
任务目标：实现 pypandoc 兼容路径，按 wkhtmltopdf -> weasyprint -> pandoc default 顺序尝试，并记录每次失败诊断。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- /home/frank/src/TradingAgents-CN/web/utils/report_exporter.py
- /home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
- /home/frank/src/TradingAgents-CN/Dockerfile.backend
- /home/frank/src/TradingAgents-CN/docs/docker/pdf-export-support.md
- src/claw_trade/ui_backend/pdf_export_service.py

允许修改范围：
- src/claw_trade/ui_backend/pdf_renderer.py
- src/claw_trade/ui_backend/pdf_validation.py 仅限复用 validator
- tests/unit/ui/test_pdf_pandoc_fallback.py

禁止行为：
- 不允许把 WeasyPrint 写成 app 当前主路径。
- 不允许 pandoc 缺失时伪造成功。
- 不允许所有引擎失败后生成占位 PDF。
- 不允许丢失 engine、exception type、message、output path、output exists、output size 诊断。

验收标准：
- 引擎顺序可被单测证明。
- 单个引擎失败会继续下一个。
- 全部失败时 PdfExportRecord.state=failed, pdf_artifact_id=None。
- 成功输出仍必须经过 T4 validator。
- pypandoc extra_args 包含 --from=markdown-yaml_metadata_block。

最终汇报格式：
- 任务 ID
- changed files
- engine attempt 表
- commands + exit code
- pandoc/wkhtmltopdf/weasyprint 运行时状态
- failed path artifact 状态
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T6 Prompt

```text
任务目标：修复微信文件发送成功判定，使 OpenClaw 返回 messageId/message_id 时文件发送成功，同时严格失败 ok:false、sent:false、异常、空返回、非 mapping。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- src/claw_trade/ui_backend/channel_bridge.py
- src/claw_trade/ui_backend/report_notification_service.py
- src/claw_trade/web/openclaw_gateway.py
- third_party/openclaw/src/gateway/server-methods/send.ts
- third_party/openclaw/src/infra/outbound/deliver-types.ts

允许修改范围：
- src/claw_trade/ui_backend/channel_bridge.py
- tests/unit/ui/test_channel_bridge.py
- 必要时 tests/unit/ui/test_report_file_send.py

禁止行为：
- 不允许文件发送使用 default_sent 兜底成功。
- 不允许把空 dict、None、字符串 "ok" 判为文件发送成功。
- 不允许吞掉 OpenClaw 异常。
- 不允许修改 OpenClaw source；若实测 OpenClaw payload 本身错误，停止并问人。

验收标准：
- {"messageId": "m-1"} 成功。
- {"message_id": "m-1"} 成功。
- {"sent": true, "messageId": "m-1"} 成功。
- {"sent": false, "messageId": "m-1"} 失败。
- {"ok": false, "error": "..."} 失败。
- {}, None, "ok" 失败。
- text send 可以保留 default_sent=True 兼容语义；file send 不可以。

最终汇报格式：
- 任务 ID
- changed files
- 判定矩阵
- commands + exit code
- OpenClaw payload 证据引用
- default_sent 是否仍用于文件发送：yes/no
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T7 Prompt

```text
任务目标：集成完整报告文件请求路径，确保只有 ready 且存在的真实 PDF artifact 才会发送；成功后不补发失败文案。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- src/claw_trade/ui_backend/report_notification_service.py
- src/claw_trade/ui_backend/report_repository.py
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/channel_bridge.py
- src/claw_trade/web/routes_ui.py

允许修改范围：
- src/claw_trade/ui_backend/report_notification_service.py
- src/claw_trade/ui_backend/report_repository.py 仅限 artifact path/read 校验窄改
- tests/unit/ui/test_report_file_send.py
- tests/unit/ui/test_report_notification_service.py
- tests/e2e/ui/test_report_user_flows.py

禁止行为：
- 不允许 PDF 未 ready 时调用 channel send。
- 不允许 PDF artifact 路径不存在时发送。
- 不允许因为 PDF ready 就假设微信发送成功。
- 不允许成功发送后再返回“完整报告文件暂不可发送”。

验收标准：
- PDF failed 返回 FILE_SEND_UNSUPPORTED，且 send spy 未被调用。
- artifact path missing/read failure 返回 FILE_SEND_UNSUPPORTED。
- channel 返回 messageId 成功，返回 sent:false/ok:false/空返回失败。
- HTTP 路径不暴露 localPath。

最终汇报格式：
- 任务 ID
- changed files
- 场景矩阵：PDF 状态 x artifact 状态 x channel 返回
- commands + exit code
- 成功后失败文案是否出现：yes/no
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

### T8 Prompt

```text
任务目标：做真实端到端验收，证明正式 Markdown 能生成真实 PDF 并通过微信文件发送，OpenClaw 返回 messageId 后不再补发失败文案。

必读文件：
- AGENTS.md
- docs/PDF渲染方案.md
- memory/ 中最新 fixed runtime 指引
- scripts/start-control-runtime.sh
- src/claw_trade/ui_backend/pdf_export_service.py
- src/claw_trade/ui_backend/report_notification_service.py
- src/claw_trade/ui_backend/channel_bridge.py
- src/claw_trade/web/openclaw_gateway.py
- third_party/openclaw/src/gateway/server-methods/send.ts

允许修改范围：
- 原则上不改业务代码。
- 如缺少可复用验收入口，只允许新增窄 live test 或证据脚本。

禁止行为：
- 不允许 mock/stub/fake 微信发送。
- 不允许绕过 scripts/start-control-runtime.sh -- <command>。
- 不允许 live subagent 自证 runtime ready；必须使用主 agent 提供的 preflight 表。
- 不允许无 messageId 却宣称文件发送成功。

验收标准：
- 主 agent 已打印 fixed runtime profile 与 preflight 表。
- 生成当前运行 PDF 文件路径。
- file 命令显示 PDF document。
- xxd 前 8 字节显示 %PDF-。
- pdfplumber 页数 >= 1，提取文本包含报告标题。
- Markdown 原文 hash 导出前后不变。
- OpenClaw send 返回 messageId。
- 微信发送成功后没有补发失败文案。

最终汇报格式：
- 任务 ID
- runtime preflight 表
- commands + exit code
- PDF 文件路径
- file/xxd/pdfplumber/hash 证据
- OpenClaw payload
- 微信/通知日志中是否出现失败补发：yes/no
- HITL 操作记录
- mock/stub/fake/fallback 状态
- memory/YYYY-MM-DD.md 追加情况
```

## Integration Plan

- 可并行启动：T1、T2、T6。T1 解决依赖和运行时能力，T2 解决清理副本，T6 独立修微信发送判定。
- T3 依赖 T2 的 cleaner 输出。
- T4 必须等 T1、T2、T3 完成后接入真实 PDF 主路径和 validator。
- T5 可在 T4 validator 接口稳定后并行补兼容路径，但不能绕过 T4 校验。
- T7 必须等 T4 和 T6 完成后做请求路径集成，重点复审“不 ready 不发送”和“messageId 成功不补失败”。
- T8 必须串行最后执行；主 agent 先按 AGENTS 做 runtime preflight，再把 preflight 表交给 live agent。
- 主 agent 需要复审：依赖分类是否把 WeasyPrint 放错位置、cleaner 是否回写 Markdown、PDF 是否真实可打开、artifact ready 是否只在校验后、文件发送是否无 `default_sent` 兜底。
- 最终验收顺序：单元测试 cleaner/HTML/validator -> 真实 PDF 导出 -> 发送判定矩阵 -> 通知集成/e2e 非 live -> live preflight -> 真实微信文件发送证据。

## Stop Conditions

- `wkhtmltopdf` 不可用且主路径无法生成真实 PDF。
- 中文字体不可用且输出乱码/空白，不能标 ready。
- `pandoc` 不可用但兼容路径被调用，且没有明确 failed 诊断。
- PDF bytes 不是 `%PDF-`、无法打开、页数为 0、提取不到标题或正文。
- 图片应该存在但无法加载，且没有 root-cause 证据。
- OpenClaw 返回 `ok:false`、`sent:false`、非 mapping、空返回或抛异常。
- 需要修改 OpenClaw source 才能完成，但修改点不是通用 runtime seam。
- 任何实现想继续使用假 PDF、空 PDF、改后缀 PDF、fallback success。
- 同一失败类别连续两次 focused fix 后仍失败。
- 真实微信验收缺少扫码、目标账号或 HITL 条件。
