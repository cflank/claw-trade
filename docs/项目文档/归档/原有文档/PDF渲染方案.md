# PDF 渲染方案

## 结论

微信 PDF 空白的根因是当前 `claw-trade` 没有生成真实 PDF，而是把 Markdown 文本前面拼了 `PDF\n\n` 后写成 `.pdf` 文件。

本方案要求按 `TradingAgents-CN` 的报告导出思路重做 PDF 渲染链路：

```text
已保存正式 Markdown
-> 生成 PDF 渲染副本
-> 按本方案定义的清理规则处理渲染副本
-> Markdown 转 HTML
-> 套中文字体、A4、横排、表格分页、图片缩放 CSS
-> 使用真实 PDF 引擎生成 PDF bytes
-> 校验 PDF 文件头、页数、可提取正文
-> 保存 PDF artifact
-> 微信发送
-> 用 OpenClaw 实际成功载荷判定发送成功
```

禁止再使用任何假 PDF、空 PDF、改后缀、占位 PDF、发送失败伪成功、发送成功误判失败。

## TradingAgents-CN 参考证据

本方案以本机 `TradingAgents-CN` 源码为准。

### 旧 Web/Streamlit 路径

参考文件：

```text
/home/frank/src/TradingAgents-CN/web/utils/report_exporter.py
```

关键行为：

1. 依赖 `markdown`、`pypandoc`。
2. 启动时检查 `pandoc`，缺失时尝试 `pypandoc.download_pandoc()`。
3. PDF 导出前调用 `_clean_markdown_for_pandoc()`。
4. 使用 `pypandoc.convert_text(..., 'pdf')` 生成 PDF。
5. PDF 引擎顺序：

```text
wkhtmltopdf
-> weasyprint
-> pandoc default
```

6. 每个引擎失败时记录错误，继续尝试下一个。
7. 所有引擎失败时返回明确错误，不生成假 PDF。

### 新后端路径

参考文件：

```text
/home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
```

关键行为：

1. 模块导入 `markdown`、`pypandoc`、`pdfkit`；其中当前 PDF 生成实际使用 `markdown` 和 `pdfkit + wkhtmltopdf`。
2. 检查 `pdfkit + wkhtmltopdf` 是否可用。
3. `generate_pdf_report()` 生成 Markdown 后直接调用 `_markdown_to_html(md_content)`。
4. PDF 主路径没有调用 `_clean_markdown_for_pandoc()`。
5. 使用 `markdown.markdown()` 转 HTML，扩展包括：

```text
tables
fenced_code
nl2br
```

6. 给 HTML 套完整模板和 CSS：

```text
html lang="zh-CN" dir="ltr"
meta charset="UTF-8"
中文字体
A4 页面
20mm 页边距
横排文本
表格分页控制
图片 max-width: 100%
代码块自动换行
页码
```

7. 使用 `pdfkit.from_string(html_content, False, options=...)` 生成 PDF bytes。
8. 失败时抛明确错误，不写假文件。

新后端文件里也有 `_clean_markdown_for_pandoc()`，但当前源码只在 Word 导出路径调用它。`claw-trade` 如果在 PDF 主路径前加入合并清理，这是本项目为防止空白/竖排/解析问题做的增强，不是 TradingAgents-CN app PDF 主路径的既有行为。

### Docker/部署参考

参考文件：

```text
/home/frank/src/TradingAgents-CN/Dockerfile.backend
/home/frank/src/TradingAgents-CN/docs/docker/pdf-export-support.md
```

当前源码和文档存在冲突，必须以源码为准：

```text
当前 Dockerfile.backend 安装：
- pandoc
- wkhtmltopdf
- fonts-noto-cjk
- fontconfig
- xvfb
- Python 包 pdfkit

当前 app/utils/report_exporter.py 使用：
- markdown
- pdfkit + wkhtmltopdf

docs/docker/pdf-export-support.md 声称：
- Docker 支持 WeasyPrint 优先
- Python 安装 weasyprint + pdfkit
```

结论：

```text
WeasyPrint 不能写成当前新后端主路径。
WeasyPrint 不能写成当前 Dockerfile.backend 已安装的必需依赖。
旧 Web/Streamlit pypandoc 兼容路径可尝试 weasyprint 作为 pandoc PDF 引擎。
依赖和验收以当前源码为准，文档声称但源码未落实的能力只能标为可选/待落实。
```

`claw-trade` 必须把主路径必需依赖、兼容路径依赖、可选依赖分别记录清楚。缺少当前路径关键工具时，PDF 状态必须是 failed，不能输出假 ready。

## 当前问题

当前代码：

```python
return ("PDF\n\n" + markdown).encode("utf-8")
```

这不是 PDF。

真实 PDF 必须至少满足：

```text
文件头是 %PDF-
PDF 阅读器能打开
至少 1 页
正文可提取
包含报告标题或正文关键词
```

## 目标状态

### 用户可见目标

1. 微信收到的完整报告文件能正常打开。
2. PDF 内容是完整报告，不是空白页。
3. 表格、标题、中文、列表、代码块、图片都能正常显示。
4. 发送成功后不再补发“完整报告文件暂不可发送”。
5. 如果 PDF 真的生成失败，用户只收到明确失败提示，不收到假文件。

### 工程目标

1. PDF 导出只使用已保存正式 Markdown。
2. PDF 渲染副本按本方案定义的 Web/app cleaner 合并规则处理。
3. 渲染副本不回写正式 Markdown。
4. 生成 PDF 必须走真实引擎。
5. PDF artifact 只有通过校验后才能进入 `ready`。
6. 微信发送成功判定必须和 OpenClaw 返回协议对齐。
7. 所有引擎、清理、校验、发送判定必须有测试覆盖。

## Markdown 清理规则

这一块不是 TradingAgents-CN app PDF 主路径的现状。当前 CN app PDF 主路径直接把 Markdown 转 HTML。

`claw-trade` 仍要在 PDF 渲染副本上做清理，这是本项目增强，来源分两部分：

```text
Web cleaner：/home/frank/src/TradingAgents-CN/web/utils/report_exporter.py
app cleaner：/home/frank/src/TradingAgents-CN/app/utils/report_exporter.py
```

规则只能作用于“送进 PDF 引擎的临时渲染副本”。正式保存的 Markdown 是报告真相源，不在 PDF 导出过程中被回写。

### 清理来源边界

```text
Web 旧路径：cleaned Markdown -> pypandoc -> PDF。
app 新路径：Markdown -> HTML -> pdfkit -> wkhtmltopdf -> PDF。
claw-trade 增强：在 app 风格主路径前生成已清理渲染副本，再 Markdown -> HTML -> pdfkit。
```

不能把 `claw-trade` 的合并清理写成 TradingAgents-CN app PDF 主路径已经存在的行为。

### YAML 元数据块保护

Pandoc 容易把开头的 `---` 或 `...` 当成 YAML 元数据边界。该规则来自 Web cleaner；app cleaner 只处理开头 `---`。

规则：

```text
content = content.strip()
如果第一行以 --- 或 ... 开头，在前面补一个空行
Pandoc 参数使用 --from=markdown-yaml_metadata_block
```

### 表格分隔符保护

TradingAgents-CN Web cleaner 在替换三连字符前会先保护表格分隔符。

规则：

```text
先保护 |------|------|
先保护 |------|
再替换其他 ---
再恢复表格分隔符
```

目的：

```text
不要让普通分隔线触发 Pandoc YAML 问题
也不要破坏 Markdown 表格
```

### 三连字符和省略号处理

TradingAgents-CN 的 Web 路径会做：

```text
--- -> —
... -> …
```

`claw-trade` 必须在 PDF 渲染副本上保留这类处理。

Web cleaner 还包含特殊引号清理。如果文档或测试声称完整覆盖 CN 清理逻辑，必须覆盖这些引号归一化；如果暂不实现，也要标成低风险差异并纳入测试说明，不能笼统说“完整一致”。

### 横排相关 HTML 清理

TradingAgents-CN app cleaner 会清理可能导致中文竖排的 HTML 样式，但当前 app PDF 主路径没有调用它。

规则：

```text
移除含 writing-mode 的标签或样式影响
移除含 text-orientation 的标签或样式影响
移除 div/span 上任意 style 属性
移除 style 块
保留普通 Markdown 结构
```

### 标题兜底

TradingAgents-CN Web cleaner 会确保导出内容以 Markdown 标题开始。

规则：

```text
如果渲染副本不以 # 开头，补 "# 分析报告"
```

该规则只作用于 PDF 渲染副本，不能回写正式报告文件。

该规则来自 Web cleaner；app cleaner 当前没有标题兜底。

## HTML 渲染规则

Markdown 转 HTML 必须参考 TradingAgents-CN 新后端路径。当前 CN app 传入的是原始 `md_content`；`claw-trade` 若启用渲染副本清理，传入的是 cleaned rendering copy。

使用 Python `markdown` 包：

```python
markdown.markdown(
    cleaned_markdown,
    extensions=[
        "markdown.extensions.tables",
        "markdown.extensions.fenced_code",
        "markdown.extensions.nl2br",
    ],
)
```

HTML 模板必须包含：

```html
<!DOCTYPE html>
<html lang="zh-CN" dir="ltr">
<head>
  <meta charset="UTF-8">
  <title>分析报告</title>
  <style>...</style>
</head>
<body>
  ...
</body>
</html>
```

图片路径支持报告目录内相对路径是 `claw-trade` 防空白 PDF 的项目增强，不是当前 TradingAgents-CN app/web 源码已经完整实现的行为。

要求：

```text
base_url 指向报告目录，或在 HTML 中写入 file:// base href
wkhtmltopdf 开启 enable-local-file-access
assets/chart.png 这类本地图片必须能进入 PDF
远程图片不做假成功；加载失败要记录
```

远程图片失败诊断、图片加载证据、正文可提取校验都属于 `claw-trade` 为防止假 PDF/空白 PDF 增加的验收要求，不应写成 CN 源码已有行为。

## CSS 要求

CSS 按 TradingAgents-CN 的目标完整覆盖中文报告场景。

### 页面

```css
@page {
  size: A4;
  margin: 20mm;
}
```

### 字体

```css
body {
  font-family: "Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "Arial", sans-serif;
  line-height: 1.8;
  color: #333;
  background: white;
  direction: ltr;
}
```

部署侧必须安装中文字体。参考 TradingAgents-CN：

```text
fonts-noto-cjk
fontconfig
fc-cache -fv
```

### 横排

```css
html,
body,
p,
div,
span,
td,
th,
li,
h1,
h2,
h3,
h4,
h5,
h6,
pre,
code {
  writing-mode: horizontal-tb !important;
  text-orientation: mixed !important;
  direction: ltr;
}
```

### 标题

```css
h1, h2, h3, h4, h5, h6 {
  page-break-after: avoid;
}

h1 {
  font-size: 2em;
  border-bottom: 3px solid #3498db;
  padding-bottom: 0.3em;
}

h2 {
  font-size: 1.6em;
  border-bottom: 2px solid #bdc3c7;
  padding-bottom: 0.25em;
}
```

### 表格

```css
table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.5em 0;
  font-size: 0.9em;
  page-break-inside: auto;
}

thead {
  display: table-header-group;
}

tbody {
  display: table-row-group;
}

tr {
  page-break-inside: avoid;
  page-break-after: auto;
}

th, td {
  border: 1px solid #ddd;
  padding: 8px 10px;
  text-align: left;
  word-break: break-word;
}
```

### 图片

```css
img {
  max-width: 100%;
  height: auto;
  page-break-inside: avoid;
}
```

### 代码块

```css
pre,
code {
  white-space: pre-wrap;
  word-wrap: break-word;
  word-break: break-word;
}
```

### 分页

```css
p,
li {
  orphans: 3;
  widows: 3;
}

blockquote,
pre,
img {
  page-break-inside: avoid;
}
```

## PDF 引擎策略

按当前 TradingAgents-CN 源码分层设计，不再写占位实现。

### 主路径

参考 TradingAgents-CN 新后端路径：

```text
Markdown
-> HTML
-> pdfkit
-> wkhtmltopdf
-> PDF bytes
```

`claw-trade` 主路径允许在第一步前生成 cleaned rendering copy：

```text
正式 Markdown
-> cleaned rendering copy
-> HTML
-> pdfkit
-> wkhtmltopdf
-> PDF bytes
```

这一步是本项目增强，来源是 Web cleaner 和 app cleaner 的组合，不是 CN app PDF 主路径现状。

Python 依赖：

```text
markdown
pdfkit
```

系统依赖：

```text
wkhtmltopdf
fonts-noto-cjk
fontconfig
```

`pdfkit` 选项：

```python
{
    "encoding": "UTF-8",
    "enable-local-file-access": None,
    "page-size": "A4",
    "margin-top": "20mm",
    "margin-right": "20mm",
    "margin-bottom": "20mm",
    "margin-left": "20mm",
}
```

### 兼容路径

参考 TradingAgents-CN 旧 Web 路径：

```text
pypandoc.convert_text(..., "pdf")
```

引擎顺序必须和 TradingAgents-CN 保持一致：

```text
wkhtmltopdf
-> weasyprint
-> pandoc default
```

`weasyprint` 只属于这个兼容路径的可选 PDF 引擎之一。不能把它写成当前 app 后端主路径，也不能把它写成当前 `Dockerfile.backend` 已安装依赖。

参数：

```python
extra_args = ["--from=markdown-yaml_metadata_block"]

如果指定引擎：
extra_args.append(f"--pdf-engine={engine}")
```

### 失败处理

每个引擎失败必须记录：

```text
engine
exception type
exception message
output file path if any
whether output existed
output size if existed
```

全部失败时：

```text
PdfExportRecord.state = failed
pdf_artifact_id = None
user_message = "PDF 暂不可用，完整报告仍可在设备界面查看。"
```

不能：

```text
不能写假 PDF
不能写空文件
不能把失败标成 ready
不能发送失败产物
```

## PDF 校验

PDF bytes 写入 artifact 前必须校验。

### 最低校验

```python
pdf_bytes.startswith(b"%PDF-")
len(pdf_bytes) >= minimum_pdf_size
```

`minimum_pdf_size` 不能太低，建议先设为 `1024`，测试可覆盖更小 fixture。

### 结构校验

使用现有环境里的 `pdfplumber` 或 `pypdfium2`。

必须验证：

```text
PDF 能打开
页数 >= 1
第一页能提取文本，或整份 PDF 能提取文本
提取文本包含报告标题或正文关键词
```

如果图片型 PDF 导致文本提取为空，必须有单独证据说明；默认不能把“不可提取正文”的 PDF 标成 ready。

### artifact 写入规则

只有通过校验后才能调用：

```text
ReportRepository.write_pdf_artifact(...)
```

写入后再次读取文件确认：

```text
路径存在
文件头是 %PDF-
文件大小等于写入 bytes 长度
```

## 微信发送成功判定

当前失败文案的根因是发送结果协议没对齐。

OpenClaw `send` 成功载荷参考：

```text
runId
messageId
channel
```

不一定包含：

```text
sent: true
```

因此 `claw-trade` 不能只认 `sent: true`。

### 判定规则

完整报告文件发送按这个顺序判断：

```text
1. 如果调用抛异常：失败
2. 如果返回不是 mapping：失败
3. 如果返回里有 ok: false：失败
4. 如果返回里有 sent: false：失败
5. 如果返回里有 sent: true：成功
6. 如果返回里有非空 messageId 或 message_id：成功
7. 其他情况：失败
```

文字发送可以继续使用 `default_sent=True` 之类的兼容判定，因为某些文字通道可能只返回空成功。但这个兜底不能用于完整报告文件发送。文件发送不能保留 `default_sent` 成功兜底。

### 禁止行为

```text
不能把没有 messageId 的空返回当成功
不能吞掉 OpenClaw 异常
不能因为 PDF ready 就假设微信发送成功
不能发送成功后再补发“完整报告文件暂不可发送”
```

### 发送失败文案触发条件

只有真实失败才能返回：

```text
完整报告文件暂不可发送，请在设备界面查看。
```

真实失败包括：

```text
没有文件发送能力
PDF 未 ready
PDF artifact 路径不存在
OpenClaw send 抛异常
OpenClaw 返回非 mapping
OpenClaw 明确返回 ok: false
OpenClaw 明确返回 sent: false
OpenClaw 返回中没有 messageId 且没有 sent: true
```

## 代码落点

### PDF 渲染

目标文件：

```text
src/claw_trade/ui_backend/pdf_export_service.py
```

允许拆出新模块：

```text
src/claw_trade/ui_backend/pdf_renderer.py
src/claw_trade/ui_backend/pdf_validation.py
```

建议结构：

```text
TradingAgentsCnMarkdownCleaner
TradingAgentsCnHtmlRenderer
PdfEngineResult
PdfEngineAttempt
PdfRenderError
PdfValidator
DefaultPdfRenderer
```

`DefaultPdfRenderer` 不再是占位实现，必须调用真实引擎。

### PDF 仓库

目标文件：

```text
src/claw_trade/ui_backend/report_repository.py
```

只负责写入已校验 PDF bytes。

不负责：

```text
不清理 Markdown
不判断 PDF 引擎
不伪造 artifact
```

### 微信发送判定

目标文件：

```text
src/claw_trade/ui_backend/channel_bridge.py
src/claw_trade/ui_backend/report_notification_service.py
```

修改 `_to_send_result` 或文件发送调用侧，让 OpenClaw 的 `messageId` 成功载荷被识别为成功，并让完整报告文件发送禁用 `default_sent` 成功兜底。

## 依赖和部署

### Python 依赖：主路径必需

`pyproject.toml` 增加：

```toml
"markdown>=3.4.0",
"pdfkit>=1.0.0",
```

说明：

```text
markdown：Markdown 转 HTML
pdfkit：TradingAgents-CN 新后端 PDF 主路径
```

### Python 依赖：兼容路径必需

如果实现旧 Web/Streamlit 兼容路径，增加：

```toml
"pypandoc>=1.11",
```

说明：

```text
pypandoc：调用 pandoc，把 cleaned Markdown 转 PDF。
```

### Python 依赖：可选/文档声称但源码未落实

```toml
"weasyprint>=60.0",
```

说明：

```text
weasyprint：旧 Web pypandoc 兼容路径中的一个可尝试 PDF 引擎。
```

当前 `TradingAgents-CN/docs/docker/pdf-export-support.md` 声称 Docker 支持 WeasyPrint 优先，但当前 `Dockerfile.backend` 没有安装 WeasyPrint 系统依赖或 Python 包，当前 `app/utils/report_exporter.py` 也没有使用 WeasyPrint。除非本仓库明确实现并验证该路径，否则 WeasyPrint 只能是可选项。

`python-docx` 属于 TradingAgents-CN 的 Word 导出后处理，不是 PDF 必需依赖；除非本仓库同时实现 Word 导出，否则不进入本 PDF 方案。

### 系统依赖：主路径必需

运行环境必须提供：

```text
wkhtmltopdf
fonts-noto-cjk
fontconfig
```

部署脚本或运行手册必须能检查：

```bash
wkhtmltopdf --version
fc-match "Noto Sans CJK SC"
```

如果中文字体不可用，PDF 导出应标记失败或至少记录明确诊断；不能默默产出乱码/空白 PDF 后标记 ready。

### 系统依赖：兼容路径必需

如果启用旧 Web/Streamlit 兼容路径，运行环境还必须提供：

```text
pandoc
```

部署脚本或运行手册必须能检查：

```bash
pandoc --version
```

### 系统依赖：可选/资料冲突项

WeasyPrint 若作为兼容路径引擎启用，需要另行落实并验证系统依赖，例如 Cairo/Pango/GDK Pixbuf 等。当前 CN `Dockerfile.backend` 没有这些安装步骤，不能把它列为已满足的 Docker 能力。

## 测试方案

### 单元测试：Markdown 清理

覆盖：

```text
开头 ---
开头 ...
普通 ---
普通 ...
Markdown 表格 |------|
Markdown 表格 |------|------|
含 writing-mode 的 HTML
含 text-orientation 的 HTML
div/span style
style 块
Web cleaner 特殊引号清理
不以 # 开头的内容补标题
```

断言：

```text
表格分隔符仍存在
YAML 风险被处理
横排风险 HTML 被清理
清理只作用于副本，repository markdown hash 不变
```

### 单元测试：HTML 渲染

覆盖：

```text
中文标题
多级标题
表格
列表
代码块
图片
引用
```

断言：

```text
html lang="zh-CN" dir="ltr"
meta charset="UTF-8"
包含中文字体 CSS
包含 A4 @page
包含表格分页 CSS
包含图片 max-width CSS
```

### 单元测试：PDF 校验

覆盖：

```text
非 PDF bytes 失败
空 PDF bytes 失败
PDF header 不对失败
页数为 0 失败
可打开但无正文失败
真实 PDF 成功
```

### 集成测试：PDF 导出

流程：

```text
保存一份带中文、表格、图片的正式 Markdown
调用 export_saved_markdown_to_pdf
读取 artifact path
验证文件头 %PDF-
用 pdfplumber 打开
验证页数 >= 1
验证提取文本包含报告标题
验证原始 Markdown hash 不变
```

### 单元测试：发送成功判定

覆盖 OpenClaw 返回：

```python
{"messageId": "m-1", "channel": "openclaw-weixin"}
{"message_id": "m-1"}
{"sent": True, "messageId": "m-1"}
{"sent": False, "messageId": "m-1"}
{"ok": False, "error": "..."}
{}
None
"ok"
```

断言：

```text
messageId 成功
sent false 优先失败
ok false 优先失败
空返回不成功
非 mapping 不成功
文件发送不使用 default_sent 成功兜底
```

### 端到端测试

真实运行一份报告：

```text
生成正式 Markdown
导出 PDF
确认 artifact 是真实 PDF
发送微信文件
OpenClaw gateway 日志出现 send success
UI/通知服务不再补发失败文案
```

## 验收标准

完成后必须给出以下证据：

```text
1. 当前运行生成的 PDF 文件路径
2. file 命令显示是 PDF document
3. xxd 前 8 字节显示 %PDF-
4. pdfplumber 页数 >= 1
5. 提取文本包含报告标题
6. Markdown 原文 hash 导出前后不变
7. OpenClaw 返回 messageId 的测试通过
8. 微信发送成功后没有补发失败文案
```

## 停止条件

遇到以下情况必须停止并报告，不能继续伪造成功：

```text
wkhtmltopdf 不可用
pandoc 不可用且兼容路径被调用
中文字体不可用且渲染结果乱码/空白
PDF bytes 不是 %PDF-
PDF 无法打开
PDF 页数为 0
PDF 提取不到报告标题或正文
图片应该存在但无法加载
OpenClaw 返回 ok: false
OpenClaw 抛发送异常
同一失败类别连续两次修复后仍失败
```

## 不允许的实现

```text
不允许继续 return ("PDF\n\n" + markdown).encode("utf-8")
不允许直接把 Markdown 文件改名成 .pdf
不允许生成空白 PDF 后标记 ready
不允许 PDF 失败后仍发送文件
不允许丢掉本方案定义的 Web/app cleaner 合并清理规则
不允许自建另一套未验证的清理规则
不允许把发送成功只绑定 sent: true
不允许把 messageId 成功载荷误判为失败
不允许完整报告文件发送使用 default_sent 兜底成功
```

## 实施顺序

```text
1. 增加依赖和系统依赖检查
2. 实现 PDF 渲染副本清理器
3. 实现 TradingAgents-CN HTML 模板和 CSS
4. 实现 pdfkit + wkhtmltopdf 主路径
5. 实现 pypandoc 引擎顺序兼容路径
6. 实现 PDF 校验
7. 替换当前占位 DefaultPdfRenderer
8. 修复 OpenClaw messageId 发送成功判定
9. 补齐单元测试和集成测试
10. 真实导出并发送微信验证
11. 记录证据和失败诊断
```

本方案完成后，PDF 导出才可以从“占位功能”升级为“真实可发送文件功能”。
