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
    const cdpTimeout = process.env.PLAYWRIGHT_WIN_CHROME_CDP_TIMEOUT_MS;
    const args = server.args.map((arg) =>
      typeof arg === "string" && arg.includes("--cdp-endpoint ")
        ? arg.replace(/--cdp-endpoint\s+\S+/, `--cdp-endpoint ${cdpEndpoint}`)
        : arg
    );
    if (cdpTimeout && !args.some((arg) => String(arg).includes("--cdp-timeout"))) {
      const commandIndex = args.findIndex((arg) => typeof arg === "string" && arg.includes("@playwright/mcp"));
      if (commandIndex >= 0) {
        args[commandIndex] = `${args[commandIndex]} --cdp-timeout ${cdpTimeout}`;
      }
    }
    const extraArgs = process.env.PLAYWRIGHT_WIN_CHROME_EXTRA_ARGS;
    if (extraArgs) {
      const commandIndex = args.findIndex((arg) => typeof arg === "string" && arg.includes("@playwright/mcp"));
      if (commandIndex >= 0) {
        args[commandIndex] = `${args[commandIndex]} ${extraArgs}`;
      }
    }
    return {
      ...server,
      args,
    };
  }
  return server;
}

function requestOptions() {
  const timeout = Number.parseInt(process.env.PLAYWRIGHT_WIN_CHROME_REQUEST_TIMEOUT_MS || "", 10);
  return Number.isFinite(timeout) && timeout > 0 ? { timeout } : undefined;
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
      const result = await client.listTools(undefined, requestOptions());
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
      const result = await client.callTool({ name: toolName, arguments: args }, undefined, requestOptions());
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
