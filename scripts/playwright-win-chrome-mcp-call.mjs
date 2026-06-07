#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import { Client } from "../third_party/openclaw/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js";
import { StdioClientTransport } from "../third_party/openclaw/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js";

function usage() {
  console.error(`Usage:
  scripts/playwright-win-chrome-mcp-call.mjs list-tools
  scripts/playwright-win-chrome-mcp-call.mjs call <tool-name> '<json-args>'

Examples:
  scripts/playwright-win-chrome-mcp-call.mjs call browser_tabs '{"action":"list"}'`);
}

function loadServerConfig() {
  const configPath = path.join(os.homedir(), ".mcp.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const server = config?.mcpServers?.["playwright-win-chrome"];
  if (!server?.command || !Array.isArray(server.args)) {
    throw new Error("Missing mcpServers.playwright-win-chrome in ~/.mcp.json");
  }
  const cdpEndpoint = process.env.PLAYWRIGHT_WIN_CHROME_CDP_ENDPOINT;
  if (cdpEndpoint) {
    return {
      ...server,
      args: server.args.map((arg) =>
        typeof arg === "string" && arg.includes("--cdp-endpoint ")
          ? arg.replace(/--cdp-endpoint\s+\S+/, `--cdp-endpoint ${cdpEndpoint}`)
          : arg
      ),
    };
  }
  return server;
}

async function main() {
  const [action, toolName, rawArgs = "{}"] = process.argv.slice(2);
  if (!action || action === "--help" || action === "-h") {
    usage();
    process.exit(action ? 0 : 2);
  }

  const server = loadServerConfig();
  const transport = new StdioClientTransport({
    command: server.command,
    args: server.args,
  });
  const client = new Client(
    { name: "claw-trade-playwright-win-chrome-client", version: "0.1.0" },
    { capabilities: {} },
  );

  await client.connect(transport);
  try {
    if (action === "list-tools") {
      const result = await client.listTools();
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    if (action === "call") {
      if (!toolName) {
        usage();
        process.exitCode = 2;
        return;
      }
      const args = JSON.parse(rawArgs);
      const result = await client.callTool({ name: toolName, arguments: args });
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    usage();
    process.exitCode = 2;
  } finally {
    await client.close();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
