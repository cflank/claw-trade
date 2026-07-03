#!/usr/bin/env node

import readline from "node:readline";
import process from "node:process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

function resolveGatewayRuntimeModule() {
  const candidates = [];
  if (process.env.OPENCLAW_PACKAGE_DIR) {
    candidates.push(
      join(process.env.OPENCLAW_PACKAGE_DIR, "dist", "plugin-sdk", "gateway-runtime.js"),
    );
  }
  const here = dirname(fileURLToPath(import.meta.url));
  candidates.push(
    join(here, "..", "runtime", "openclaw", "dist", "plugin-sdk", "gateway-runtime.js"),
    join(here, "..", "third_party", "openclaw", "dist", "plugin-sdk", "gateway-runtime.js"),
  );
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return pathToFileURL(candidate).href;
    }
  }
  throw new Error(`OpenClaw gateway runtime module not found: ${candidates.join(", ")}`);
}

const { GatewayClient, startGatewayClientWhenEventLoopReady } = await import(
  resolveGatewayRuntimeModule()
);

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

function writeResponse(response) {
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

let activeClient = null;

function readString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readPositiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function readConfig(request = {}) {
  const timeoutMs = readPositiveInt(
    request.timeoutMs ?? process.env.OPENCLAW_GATEWAY_TIMEOUT_MS,
    10000,
  );
  return {
    url: readString(request.url) ?? readString(process.env.OPENCLAW_GATEWAY_URL),
    token: readString(request.token) ?? readString(process.env.OPENCLAW_GATEWAY_TOKEN),
    password: readString(request.password) ?? readString(process.env.OPENCLAW_GATEWAY_PASSWORD),
    timeoutMs,
  };
}

function configKey(config) {
  return JSON.stringify({
    url: config.url ?? null,
    token: config.token ?? null,
    password: config.password ?? null,
  });
}

function closeActiveClient() {
  if (activeClient?.client) {
    activeClient.client.stop();
  }
  activeClient = null;
}

function createClient(config) {
  const key = configKey(config);
  let settled = false;
  let client;
  const options = {
    url: config.url,
    token: config.token,
    password: config.password,
    requestTimeoutMs: config.timeoutMs,
    connectChallengeTimeoutMs: Math.min(config.timeoutMs, 15000),
    clientName: "cli",
    clientDisplayName: "claw-trade-ui-chat",
    mode: "cli",
    role: "operator",
    scopes: ["operator.read", "operator.write"],
    onHelloOk: () => {
      settled = true;
      activeClient?.resolve?.();
    },
    onConnectError: (error) => {
      if (!settled) {
        settled = true;
        activeClient?.reject?.(error);
      }
    },
    onClose: (_code, reason) => {
      if (!settled) {
        settled = true;
        activeClient?.reject?.(new Error(`gateway closed before ready: ${reason}`));
      }
    },
  };
  const ready = new Promise((resolve, reject) => {
    client = new GatewayClient(options);
    activeClient = { key, client, ready: null, resolve, reject };
    void startGatewayClientWhenEventLoopReady(client, {
      timeoutMs: Math.min(config.timeoutMs, 15000),
      clientOptions: options,
    }).catch((error) => {
      if (!settled) {
        settled = true;
        reject(error);
      }
    });
  });
  activeClient.ready = ready;
  return activeClient;
}

async function ensureClient(config) {
  const key = configKey(config);
  if (!activeClient || activeClient.key !== key) {
    closeActiveClient();
    createClient(config);
  }
  await activeClient.ready;
  return activeClient.client;
}

async function requestGateway(request) {
  const method = String(request.method || "");
  if (!method) {
    throw new Error("missing gateway method");
  }
  const config = readConfig(request);
  const params = request.params && typeof request.params === "object" ? request.params : {};
  const expectFinal = request.expectFinal === true;
  try {
    const client = await ensureClient(config);
    return await client.request(method, params, {
      expectFinal,
      timeoutMs: config.timeoutMs,
    });
  } catch (error) {
    closeActiveClient();
    const message = error instanceof Error ? error.message : String(error);
    if (!/gateway not connected|gateway closed|gateway client stopped/i.test(message)) {
      throw error;
    }
    const client = await ensureClient(config);
    return await client.request(method, params, {
      expectFinal,
      timeoutMs: config.timeoutMs,
    });
  }
}

void ensureClient(readConfig()).catch(() => {
  closeActiveClient();
});

for await (const line of input) {
  if (!line.trim()) {
    continue;
  }
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    writeResponse({
      id: null,
      ok: false,
      error: { message: `invalid JSON request: ${String(error)}` },
    });
    continue;
  }

  const id = typeof request.id === "string" ? request.id : null;
  try {
    const result = await requestGateway(request);
    writeResponse({ id, ok: true, result });
  } catch (error) {
    writeResponse({
      id,
      ok: false,
      error: { message: error instanceof Error ? error.message : String(error) },
    });
  }
}
