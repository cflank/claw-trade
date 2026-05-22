# claw-trade Win11 Chrome MCP 固定执行规范

本文档冻结 `claw-trade` 仓库自己的 Win11 Chrome 调试入口。后续极简 UI 浏览器验收必须走这里定义的路径。

## 唯一路径

```text
Codex / WSL
  -> ~/.mcp.json
  -> MCP server: playwright-win-chrome
  -> Windows-side Playwright MCP
  -> CDP http://127.0.0.1:9222
  -> Win11 Chrome
```

固定含义：

- Chrome 必须是真实 Win11 Chrome。
- 页面控制必须经过 `playwright-win-chrome` MCP。
- 不允许用 WSL Chromium、headless、raw Playwright 直连 CDP、临时 PowerShell/Node 脚本替代 MCP 控制。

## 仓库入口

检查：

```bash
scripts/mcp-chrome.sh --check
```

启动：

```bash
scripts/mcp-chrome.sh --start
```

只有看到以下任一结果，才允许继续浏览器验收：

- `READY on Windows`
- `READY from WSL`

## MCP 配置

用户级 MCP 配置文件仍是 `~/.mcp.json`，服务名固定为 `playwright-win-chrome`。

当前固定配置：

```json
{
  "mcpServers": {
    "playwright-win-chrome": {
      "command": "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
      "args": [
        "-NoProfile",
        "-Command",
        "Set-Location 'C:\\Windows\\Temp'; & 'C:\\Program Files\\nodejs\\npx.cmd' -y @playwright/mcp@latest --cdp-endpoint http://127.0.0.1:9222"
      ]
    }
  }
}
```

## 执行规则

1. 先运行 `scripts/mcp-chrome.sh --check`。
2. 未 ready 时运行 `scripts/mcp-chrome.sh --start`。
3. ready 后只通过 `playwright-win-chrome` MCP 做页面打开、点击、输入、读取和截图。
4. 如果当前会话没有暴露页面动作工具，只能补 MCP client 连接同一个 `playwright-win-chrome` 服务。
5. 固定路径不可用时报告 blocker，不切换到其它浏览器自动化方式。
