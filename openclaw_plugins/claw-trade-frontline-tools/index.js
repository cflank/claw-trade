import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { definePluginEntry } from "../../third_party/openclaw/dist/plugin-sdk/plugin-entry.js";

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(PLUGIN_DIR, "..", "..");
const TOOL_NAMES = Object.freeze({
  clawGetMarketPack: "claw_get_market_pack",
  clawGetFundamentalPack: "claw_get_fundamental_pack",
  clawGetNewsPack: "claw_get_news_pack",
  clawGetSocialPack: "claw_get_social_pack",
  clawGetPolicyPack: "claw_get_policy_pack",
  clawGetHotMoneyPack: "claw_get_hot_money_pack",
  clawGetLockupPack: "claw_get_lockup_pack",
});
const FRONTLINE_STAGE = "frontline";
const DATA_PACK_DOMAIN_MARKET = "market";
const DATA_PACK_DOMAIN_FUNDAMENTAL = "fundamental";
const DATA_PACK_DOMAIN_NEWS = "news";
const DATA_PACK_DOMAIN_SOCIAL = "social";
const DATA_PACK_DOMAIN_POLICY = "policy";
const DATA_PACK_DOMAIN_HOT_MONEY = "hot_money";
const DATA_PACK_DOMAIN_LOCKUP = "lockup";
const MARKET_CN_A = "CN_A";
const MARKET_HK = "HK";
const MARKET_US = "US";
const MARKET_CRYPTO = "CRYPTO";
const ALL_MARKETS = [MARKET_CN_A, MARKET_HK, MARKET_US, MARKET_CRYPTO];
const TOOL_ERROR_CODES = Object.freeze({
  runtimeContextMissing: "TOOL_RUNTIME_CONTEXT_MISSING",
  paramsInvalid: "TOOL_PARAMS_INVALID",
  workerMismatch: "TOOL_WORKER_MISMATCH",
  contextIncomplete: "TOOL_CONTEXT_INCOMPLETE",
  subprocessTimeout: "TOOL_SUBPROCESS_TIMEOUT",
  protocolError: "TOOL_PROTOCOL_ERROR",
});
const DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS = 30000;
const DEFAULT_MARKET_PACK_TIMEOUT_MS = 120000;
const DEFAULT_CRYPTO_MARKET_PACK_TIMEOUT_MS = 120000;
const DEFAULT_NEWS_TOTAL_TIMEOUT_MS = 20000;
const DEFAULT_SOCIAL_PACK_TIMEOUT_MS = 20000;
const DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS = 25000;
const SUBPROCESS_TIMEOUT_BUFFER_MS = 5000;
const STDERR_SUMMARY_MAX_CHARS = 2000;
const STDOUT_SUMMARY_MAX_CHARS = 2000;
const WECHAT_CHANNEL_ID = "openclaw-weixin";
const UI_CHANNEL_KIND = "wechat_clawbot";
const DEFAULT_UI_INBOUND_TIMEOUT_MS = 60000;
const IMMEDIATE_INBOUND_ACK_TEXT = "收到，正在处理。";

const OPTIONAL_TEXT = {
  type: "string",
};

const PACK_INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    ticker: OPTIONAL_TEXT,
    market: OPTIONAL_TEXT,
    company_name: OPTIONAL_TEXT,
    industry: OPTIONAL_TEXT,
    start_date: OPTIONAL_TEXT,
    end_date: OPTIONAL_TEXT,
    aliases: {
      type: "array",
      items: { type: "string" },
    },
    approved_artifact_refs: {
      type: "array",
      items: {},
    },
  },
};

const DATA_PACK_INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {},
};

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function nonNegativeIntegerValue(value) {
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return undefined;
  }
  return parsed;
}

function readInboundTimeoutMs() {
  return nonNegativeIntegerValue(process.env.CLAW_TRADE_UI_INBOUND_TIMEOUT_MS) ?? DEFAULT_UI_INBOUND_TIMEOUT_MS;
}

function safeToken(value, defaultValue = "unknown") {
  const raw = textValue(value) ?? defaultValue;
  const safe = raw.replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 96);
  return safe || defaultValue;
}

function packEvidenceCallId(workerCallId, toolCallId) {
  return `${workerCallId}__tool-${safeToken(toolCallId)}`;
}

function listValue(value, fieldName) {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value)) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `params.${fieldName} must be an array`);
  }
  return value;
}

function readCommand(ctx, expectedWorkerId, toolName) {
  const command = ctx?.singleWorkerCommand;
  if (!isRecord(command)) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.runtimeContextMissing,
      `${toolName} requires ctx.singleWorkerCommand`,
      { tool_name: toolName, expected_worker_id: expectedWorkerId },
    );
  }
  const workerId = textValue(command.worker_id);
  const expectedWorkerIds = Array.isArray(expectedWorkerId) ? expectedWorkerId : [expectedWorkerId];
  if (!expectedWorkerIds.includes(workerId)) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.workerMismatch,
      `${toolName} worker mismatch: expected ${expectedWorkerIds.join(", ")}, got ${workerId ?? "<empty>"}`,
      { tool_name: toolName, expected_worker_id: expectedWorkerIds.join(","), worker_id: workerId ?? null },
    );
  }
  const runtimeVars = isRecord(command.runtime_vars) ? command.runtime_vars : {};
  const runId = textValue(command.run_id);
  const callId = textValue(command.call_id);
  const stage = textValue(command.stage);
  const evidenceDir = textValue(command.evidence_dir);
  if (!runId || !callId || !stage || !evidenceDir) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.contextIncomplete,
      `${toolName} runtime context is incomplete`,
      { tool_name: toolName, worker_id: workerId },
    );
  }
  if (stage !== FRONTLINE_STAGE) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.contextIncomplete,
      `${toolName} requires stage=${FRONTLINE_STAGE}, got ${stage}`,
      { tool_name: toolName, worker_id: workerId, stage },
    );
  }
  return { command, runtimeVars, runId, callId, stage, workerId, evidenceDir };
}

function runtimeText(runtimeVars, params, fieldName, required = false) {
  const value = readOptionalString(params, fieldName) ?? readOptionalString(runtimeVars, fieldName);
  if (required && value === undefined) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `params.${fieldName} is required`);
  }
  return value;
}

function runtimeOnlyText(runtimeVars, fieldName, required = false) {
  const value = readOptionalRuntimeString(runtimeVars, fieldName);
  if (required && value === undefined) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `runtime_vars.${fieldName} is required`);
  }
  return value;
}

function runtimeOnlyPositiveNumber(runtimeVars, fieldName) {
  const raw = runtimeVars?.[fieldName];
  if (raw === undefined || raw === null || raw === "") {
    return undefined;
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `runtime_vars.${fieldName} must be a positive number`,
    );
  }
  return value;
}

function currentDispatchCounts(dispatcher) {
  const counts = dispatcher?.getQueuedCounts?.();
  if (!isRecord(counts)) {
    return { tool: 0, block: 0, final: 0 };
  }
  return {
    tool: Number(counts.tool) || 0,
    block: Number(counts.block) || 0,
    final: Number(counts.final) || 0,
  };
}

function resolveInboundChannelId(event) {
  const ctx = isRecord(event?.ctx) ? event.ctx : {};
  const channel =
    textValue(event?.originatingChannel) ??
    textValue(ctx.OriginatingChannel) ??
    textValue(ctx.Surface) ??
    textValue(ctx.Provider);
  return channel ? channel.toLowerCase() : undefined;
}

function resolveInboundText(ctx) {
  return (
    textValue(ctx.BodyForCommands) ??
    textValue(ctx.CommandBody) ??
    textValue(ctx.RawBody) ??
    textValue(ctx.Body)
  );
}

function resolveInboundMessageId(ctx) {
  return (
    textValue(ctx.MessageSidFull) ??
    textValue(ctx.MessageSid) ??
    textValue(ctx.MessageSidFirst) ??
    textValue(ctx.MessageSidLast)
  );
}

function resolveInboundSenderId(ctx) {
  return (
    textValue(ctx.SenderId) ??
    textValue(ctx.From) ??
    textValue(ctx.SenderUsername) ??
    textValue(ctx.SenderName)
  );
}

function resolveInboundReceivedAt(ctx) {
  const timestamp = Number(ctx?.Timestamp);
  if (Number.isFinite(timestamp) && timestamp > 0) {
    return new Date(timestamp).toISOString();
  }
  return undefined;
}

function buildUiInboundPayload(event) {
  if (!isRecord(event?.ctx)) {
    return null;
  }
  if (resolveInboundChannelId(event) !== WECHAT_CHANNEL_ID) {
    return null;
  }
  const ctx = event.ctx;
  const text = resolveInboundText(ctx);
  if (!text) {
    return null;
  }
  const senderId = resolveInboundSenderId(ctx);
  if (!senderId) {
    return null;
  }
  const messageId = resolveInboundMessageId(ctx);
  const runId = textValue(event.runId) ?? "unknown-run";
  const requestId = `wechat-inbound-${runId}-${safeToken(messageId ?? senderId)}`;
  const payload = {
    requestId,
    channelKind: UI_CHANNEL_KIND,
    accountId: textValue(ctx.AccountId) ?? undefined,
    senderId,
    text,
    messageId: messageId ?? undefined,
    receivedAt: resolveInboundReceivedAt(ctx),
  };
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined));
}

function looksLikeReportRequestText(text) {
  const normalized = text.trim().toLowerCase();
  return (
    normalized === "report" ||
    normalized.startsWith("report ") ||
    normalized.startsWith("/report") ||
    text.includes("报告") ||
    text.includes("研报") ||
    text.includes("投研")
  );
}

function shouldSendImmediateInboundAck(payload) {
  const text = textValue(payload?.text);
  return Boolean(text && !looksLikeReportRequestText(text));
}

async function postInboundMessageToUi(url, payload, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) {
      console.warn(`[claw-trade-frontline-tools] inbound UI bridge HTTP ${response.status}`);
      return null;
    }
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function handleReplyDispatchHook(event, ctx) {
  const counts = currentDispatchCounts(ctx?.dispatcher);
  const inboundUrl = textValue(process.env.CLAW_TRADE_UI_INBOUND_URL);
  if (!inboundUrl) {
    return { handled: false, queuedFinal: false, counts };
  }
  const payload = buildUiInboundPayload(event);
  if (!payload) {
    return { handled: false, queuedFinal: false, counts };
  }
  const ackQueued = shouldSendImmediateInboundAck(payload)
    ? Boolean(ctx?.dispatcher?.sendFinalReply?.({ text: IMMEDIATE_INBOUND_ACK_TEXT }))
    : false;
  try {
    const inboundResult = await postInboundMessageToUi(inboundUrl, payload, readInboundTimeoutMs());
    if (!isRecord(inboundResult)) {
      return { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) };
    }
    if (inboundResult.handled !== true) {
      return { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) };
    }
    const replyText = textValue(inboundResult.replyText);
    if (!replyText) {
      console.warn("[claw-trade-frontline-tools] inbound UI bridge returned handled=true without replyText");
      return { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) };
    }
    const queuedFinal = Boolean(ctx?.dispatcher?.sendFinalReply?.({ text: replyText }));
    return {
      handled: true,
      queuedFinal: ackQueued || queuedFinal,
      counts: currentDispatchCounts(ctx?.dispatcher),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[claw-trade-frontline-tools] inbound UI bridge failed: ${message}`);
    return { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) };
  }
}

function ignoredModelInputFields(params) {
  if (!isRecord(params)) {
    return [];
  }
  return Object.keys(params).sort();
}

function buildToolInput(runtimeVars, params, requiredFields = []) {
  if (!isRecord(params)) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "tool params must be a JSON object");
  }
  const runtime = isRecord(runtimeVars) ? runtimeVars : {};
  const required = new Set(requiredFields);
  const aliases = listValue(params.aliases ?? runtime.aliases, "aliases");
  const approvedArtifactRefs = listValue(
    params.approved_artifact_refs ?? runtime.approved_artifact_refs,
    "approved_artifact_refs",
  );
  if (aliases && !aliases.every((item) => typeof item === "string")) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "params.aliases must contain only strings",
    );
  }
  const input = {
    ticker: runtimeText(runtime, params, "ticker", required.has("ticker")),
    market: runtimeText(runtime, params, "market", required.has("market")),
    company_name: runtimeText(runtime, params, "company_name", false),
    industry: runtimeText(runtime, params, "industry", false),
    start_date: runtimeText(runtime, params, "start_date", false),
    end_date: runtimeText(runtime, params, "end_date", false),
    aliases,
    approved_artifact_refs: approvedArtifactRefs,
  };
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined));
}

function buildDataPackToolInput(runtimeVars, params) {
  if (!isRecord(params)) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "tool params must be a JSON object");
  }
  const runtime = isRecord(runtimeVars) ? runtimeVars : {};
  const market = runtimeOnlyText(runtime, "market", true);
  const profile = runtimeOnlyText(runtime, "profile", false) ?? market;
  const freshnessMaxAgeSeconds = runtimeOnlyPositiveNumber(runtime, "freshness_max_age_seconds");
  const input = {
    ticker: runtimeOnlyText(runtime, "ticker", true),
    market,
    profile,
    company_name: runtimeOnlyText(runtime, "company_name", true),
    start_date: runtimeOnlyText(runtime, "start_date", true),
    end_date: runtimeOnlyText(runtime, "end_date", true),
    current_date: runtimeOnlyText(runtime, "current_date", true),
    currency: runtimeOnlyText(runtime, "currency", true),
    freshness_max_age_seconds: freshnessMaxAgeSeconds,
  };
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined));
}

function buildRuntimeContext(runtime, toolName, toolCallId) {
  const currentDate = textValue(runtime.runtimeVars.current_date);
  const startDate = textValue(runtime.runtimeVars.start_date);
  const endDate = textValue(runtime.runtimeVars.end_date);
  const reportPrefetchManifestPath = textValue(runtime.runtimeVars.report_prefetch_manifest_path);
  const workerCallId = runtime.callId;
  const providerCallId = packEvidenceCallId(workerCallId, toolCallId);
  const context = {
    run_id: runtime.runId,
    stage: runtime.stage,
    worker_id: runtime.workerId,
    call_id: providerCallId,
    dispatch_id: workerCallId,
    worker_call_id: workerCallId,
    tool_call_id: textValue(toolCallId) || null,
    tool_name: toolName,
    evidence_root: path.join(runtime.evidenceDir, "pack-tool-evidence"),
    current_date: currentDate,
    start_date: startDate,
    end_date: endDate,
    current_time: new Date().toISOString(),
  };
  if (reportPrefetchManifestPath) {
    context.report_prefetch_required = true;
    context.report_prefetch_manifest_path = reportPrefetchManifestPath;
  }
  return context;
}

function assertExpectedMarket(toolName, toolInput, expectedMarket) {
  const market = readOptionalString(toolInput, "market");
  if (!market) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `params.market is required`,
      { tool_name: toolName },
    );
  }
  const expectedMarkets = Array.isArray(expectedMarket) ? expectedMarket : [expectedMarket];
  if (!expectedMarkets.includes(market)) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `params.market must be ${expectedMarkets.join(" or ")}, got ${market}`,
      { tool_name: toolName, market },
    );
  }
}

function pythonExecutable() {
  const explicit = textValue(process.env.CLAW_TRADE_FRONTLINE_TOOL_PYTHON);
  if (explicit) {
    return explicit;
  }
  const venvPython = path.join(REPO_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

function positiveIntegerEnv(name, defaultValue) {
  const raw = textValue(process.env[name]);
  if (!raw) {
    return defaultValue;
  }
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : defaultValue;
}

function positiveSecondsEnvToMs(name, defaultMs) {
  const defaultSeconds = Math.ceil(defaultMs / 1000);
  const seconds = positiveIntegerEnv(name, defaultSeconds);
  return seconds * 1000;
}

function subprocessTimeoutMs(domainTotalTimeoutMs) {
  const totalTimeout = Number.isFinite(domainTotalTimeoutMs) && domainTotalTimeoutMs > 0
    ? domainTotalTimeoutMs
    : DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS;
  return Math.max(totalTimeout + SUBPROCESS_TIMEOUT_BUFFER_MS, DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS);
}

function providerTotalTimeoutMs() {
  return positiveIntegerEnv("CN_A_PROVIDER_TOTAL_TIMEOUT_MS", DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS);
}

function domainToolTimeoutMs(domainTotalTimeoutMs) {
  const totalTimeout = Number.isFinite(domainTotalTimeoutMs) && domainTotalTimeoutMs > 0
    ? domainTotalTimeoutMs
    : DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS;
  return Math.max(totalTimeout, providerTotalTimeoutMs());
}

function parseJsonFromStdout(stdout) {
  const trimmed = stdout.trim();
  if (!trimmed) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return undefined;
  }
}

function runPythonJson(args, payload, options = {}) {
  return new Promise((resolve) => {
    let completed = false;
    function complete(result) {
      if (completed) {
        return;
      }
      completed = true;
      resolve(result);
    }
    function appendStderr(message) {
      const text = String(message ?? "").trim();
      if (!text) {
        return;
      }
      if (stderr && !stderr.endsWith("\n")) {
        stderr += "\n";
      }
      stderr += text;
    }
    const timeoutMs = Number.isFinite(options.timeoutMs) && options.timeoutMs > 0 ? options.timeoutMs : undefined;
    if (!timeoutMs) {
      complete({
        exitCode: 1,
        stdout: "",
        stderr: "timeoutMs must be a positive integer",
        parsed: undefined,
      });
      return;
    }
    const scriptDirs = options.pythonPathDirs ?? [];
    const pythonPath = [
      REPO_ROOT,
      ...scriptDirs,
      process.env.PYTHONPATH ?? "",
    ]
      .filter(Boolean)
      .join(path.delimiter);
    const child = spawn(pythonExecutable(), args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      if (stderr && !stderr.endsWith("\n") && !String(chunk).startsWith("\n")) {
        stderr += "\n";
      }
      stderr += chunk;
    });
    child.on("error", (error) => {
      appendStderr(error.message);
      complete({
        exitCode: 127,
        stdout,
        stderr,
        parsed: undefined,
      });
    });
    child.stdin.on("error", (error) => {
      appendStderr(`stdin write failed: ${error.message}`);
    });
    let timedOut = false;
    let forceKillTimer;
    const timeoutTimer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      forceKillTimer = setTimeout(() => child.kill("SIGKILL"), 2000);
      forceKillTimer.unref?.();
    }, timeoutMs);
    timeoutTimer?.unref?.();
    child.on("close", (exitCode) => {
      if (timeoutTimer) {
        clearTimeout(timeoutTimer);
      }
      if (forceKillTimer) {
        clearTimeout(forceKillTimer);
      }
      complete({
        exitCode: timedOut ? 124 : exitCode ?? 1,
        stdout,
        stderr: timedOut ? `${stderr}\nprocess timed out after ${timeoutMs}ms`.trim() : stderr,
        parsed: timedOut ? undefined : parseJsonFromStdout(stdout),
      });
    });
    if (payload !== undefined) {
      try {
        child.stdin.write(JSON.stringify(payload));
      } catch (error) {
        appendStderr(`stdin write failed: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    try {
      child.stdin.end();
    } catch (error) {
      appendStderr(`stdin close failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  });
}

function toolResult(payload, isError = false) {
  return {
    isError,
    content: [
      {
        type: "text",
        text: modelFacingToolText(payload, isError),
      },
    ],
    details: payload,
  };
}

function modelFacingToolText(payload, isError = false) {
  if (isRecord(payload)) {
    const modelVisibleText =
      textValue(payload.model_visible_text) ??
      textValue(payload["reader" + "_brief"]) ??
      textValue(payload["reader" + "_brief_md"]);
    if (modelVisibleText) {
      return modelVisibleText;
    }
    const error = isRecord(payload.error) ? payload.error : undefined;
    if (error) {
      const code = textValue(error.code) ?? "UNKNOWN_ERROR";
      const message = textValue(error.message) ?? "工具返回失败，但未提供可读错误说明";
      return `资料包工具失败：${code}。${message}`;
    }
    if (!isError && payload.ok === true) {
      return "未提供自然语言资料包正文。请只基于可见事实写证据缺口，不要补写未提供的数据。";
    }
  }
  return isError
    ? "资料包工具失败。请在报告中说明工具失败和证据缺口，不要补写未提供的数据。"
    : "未提供自然语言资料包正文。请只基于可见事实写证据缺口，不要补写未提供的数据。";
}

class FrontlineToolError extends Error {
  constructor(code, message, details = undefined) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

function toolErrorPayload(code, message, details = undefined) {
  return {
    ok: false,
    error: {
      code,
      message,
      ...(details && isRecord(details) ? details : {}),
    },
  };
}

function toolErrorResult(code, message, details = undefined) {
  return toolResult(toolErrorPayload(code, message, details), true);
}

function readOptionalString(payload, fieldName) {
  const value = payload?.[fieldName];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "string") {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `params.${fieldName} must be a string`);
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function readOptionalRuntimeString(payload, fieldName) {
  const value = payload?.[fieldName];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "string") {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `runtime_vars.${fieldName} must be a string`);
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function redactSummary(text, maxChars = STDERR_SUMMARY_MAX_CHARS) {
  const source = String(text ?? "");
  if (!source) {
    return source;
  }
  const withoutUrlCredentials = source.replace(
    /([a-z][a-z0-9+.-]*:\/\/)([^/\s:@]+):([^@\s/]+)@/giu,
    "$1***@",
  );
  const withoutKeyValue = withoutUrlCredentials
    .replace(
      /\b(authorization|api_key|apikey|token|password|passwd|signature|sign)\b(\s*[=:]\s*)([^\s,;&]+)/giu,
      "$1$2***",
    )
    .replace(/\b[A-Za-z0-9]{20,}\b/gu, "***");
  return withoutKeyValue.slice(0, maxChars);
}

function subprocessTimeoutError(toolName, runtime, timeoutMs, stderr) {
  return toolErrorResult(
    TOOL_ERROR_CODES.subprocessTimeout,
    `${toolName} python subprocess timed out`,
    {
      tool_name: toolName,
      worker_id: runtime.workerId,
      run_id: runtime.runId,
      call_id: runtime.callId,
      timeout_ms: timeoutMs,
      stderr_summary: redactSummary(stderr, STDERR_SUMMARY_MAX_CHARS),
    },
  );
}

function protocolError(toolName, runtime, result) {
  const message = result.exitCode === 0
    ? `${toolName} python stdout is not valid JSON`
    : `${toolName} python tool failed before returning a formal result`;
  return toolErrorResult(
    TOOL_ERROR_CODES.protocolError,
    message,
    {
      tool_name: toolName,
      worker_id: runtime.workerId,
      run_id: runtime.runId,
      call_id: runtime.callId,
      exit_code: result.exitCode,
      stderr_summary: redactSummary(result.stderr, STDERR_SUMMARY_MAX_CHARS),
      stdout_summary: redactSummary(result.stdout, STDOUT_SUMMARY_MAX_CHARS),
    },
  );
}

function shouldMarkToolResultAsError(payload) {
  if (!isRecord(payload)) {
    return false;
  }
  if (payload.ok === false) {
    return true;
  }
  return isRecord(payload.error);
}

function runtimeErrorToResult(toolName, expectedWorkerId, error) {
  if (error instanceof FrontlineToolError) {
    return toolErrorResult(error.code, error.message, {
      tool_name: toolName,
      expected_worker_id: expectedWorkerId,
      ...(isRecord(error.details) ? error.details : {}),
    });
  }
  return toolErrorResult(TOOL_ERROR_CODES.protocolError, error instanceof Error ? error.message : String(error), {
    tool_name: toolName,
    expected_worker_id: expectedWorkerId,
  });
}

function dataPackScriptConfig(expectedWorkerId, packDomain) {
  return {
    expectedWorkerId,
    expectedMarket: ALL_MARKETS,
    args: [
      "-c",
      [
        "import json, sys",
        "try:",
        "    from claw_trade.reports.data_pack_bridge import run_frontline_data_pack",
        "    payload = json.load(sys.stdin)",
        "    tool_input = dict(payload.get('tool_input') or {})",
        "    runtime_context = dict(payload.get('runtime_context') or {})",
        "    tool_input.setdefault('start_date', runtime_context.get('start_date') or '')",
        "    tool_input.setdefault('end_date', runtime_context.get('end_date') or '')",
        "    tool_input.setdefault('current_date', runtime_context.get('current_date') or '')",
        "    result = run_frontline_data_pack(tool_input, runtime_context)",
        "except Exception as exc:",
        "    print(json.dumps({'ok': False, 'error': {'code': 'data_pack_runtime_blocked', 'message': str(exc)}}, ensure_ascii=False, default=str))",
        "    raise SystemExit(0)",
        "print(json.dumps(result, ensure_ascii=False, default=str))",
      ].join("\n"),
      packDomain,
    ],
    pythonPathDirs: [path.join(REPO_ROOT, "src")],
    inputBuilder: buildDataPackToolInput,
    totalTimeoutMs: domainToolTimeoutMs(providerTotalTimeoutMs()),
    packDomain,
  };
}

function resolvePackTotalTimeoutMs(config, toolInput, toolName) {
  if (toolName === TOOL_NAMES.clawGetMarketPack) {
    return domainToolTimeoutMs(DEFAULT_MARKET_PACK_TIMEOUT_MS);
  }
  return config.totalTimeoutMs;
}

const TOOL_CONFIG_FACTORIES = Object.freeze({
  [TOOL_NAMES.clawGetMarketPack]: () => dataPackScriptConfig("market_analyst", DATA_PACK_DOMAIN_MARKET),
  [TOOL_NAMES.clawGetFundamentalPack]: () => dataPackScriptConfig("fundamental_analyst", DATA_PACK_DOMAIN_FUNDAMENTAL),
  [TOOL_NAMES.clawGetNewsPack]: () => dataPackScriptConfig("news_analyst", DATA_PACK_DOMAIN_NEWS),
  [TOOL_NAMES.clawGetSocialPack]: () => dataPackScriptConfig("social_analyst", DATA_PACK_DOMAIN_SOCIAL),
  [TOOL_NAMES.clawGetPolicyPack]: () => ({
    ...dataPackScriptConfig("policy_analyst", DATA_PACK_DOMAIN_POLICY),
    expectedMarket: [MARKET_CN_A],
  }),
  [TOOL_NAMES.clawGetHotMoneyPack]: () => ({
    ...dataPackScriptConfig("hot_money_tracker", DATA_PACK_DOMAIN_HOT_MONEY),
    expectedMarket: [MARKET_CN_A],
  }),
  [TOOL_NAMES.clawGetLockupPack]: () => ({
    ...dataPackScriptConfig("lockup_watcher", DATA_PACK_DOMAIN_LOCKUP),
    expectedMarket: [MARKET_CN_A],
  }),
});

async function executeFrontlineTool(ctx, params, toolName, toolCallId) {
  const configFactory = TOOL_CONFIG_FACTORIES[toolName];
  if (!configFactory) {
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, `unknown tool config: ${toolName}`, { tool_name: toolName });
  }
  const config = configFactory();
  const runtime = readCommand(ctx, config.expectedWorkerId, toolName);
  const toolInput = config.inputBuilder
    ? config.inputBuilder(runtime.runtimeVars, params, toolName)
    : buildToolInput(runtime.runtimeVars, params, config.requiredFields);
  assertExpectedMarket(toolName, toolInput, config.expectedMarket);
  const runtimeContext = buildRuntimeContext(runtime, toolName, toolCallId);
  if (config.packDomain) {
    runtimeContext.pack_domain = config.packDomain;
  }
  const ignoredFields = ignoredModelInputFields(params);
  if (ignoredFields.length > 0) {
    runtimeContext.ignored_model_input_fields = ignoredFields;
  }
  const payload = {
    tool_input: toolInput,
    runtime_context: runtimeContext,
  };
  const timeoutMs = subprocessTimeoutMs(resolvePackTotalTimeoutMs(config, toolInput, toolName));
  const result = await runPythonJson(config.args, payload, {
    pythonPathDirs: config.pythonPathDirs,
    timeoutMs,
  });
  if (result.exitCode === 124) {
    return subprocessTimeoutError(toolName, runtime, timeoutMs, result.stderr);
  }
  if (result.parsed === undefined) {
    return protocolError(toolName, runtime, result);
  }
  return toolResult(result.parsed, shouldMarkToolResultAsError(result.parsed));
}

async function runPack(ctx, params, toolName, expectedWorkerId, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, toolName, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(toolName, expectedWorkerId, error);
  }
}

async function runClawGetMarketPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetMarketPack, "market_analyst", toolCallId);
}

async function runClawGetFundamentalPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetFundamentalPack, "fundamental_analyst", toolCallId);
}

async function runClawGetNewsPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetNewsPack, "news_analyst", toolCallId);
}

async function runClawGetSocialPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetSocialPack, "social_analyst", toolCallId);
}

async function runClawGetPolicyPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetPolicyPack, "policy_analyst", toolCallId);
}

async function runClawGetHotMoneyPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetHotMoneyPack, "hot_money_tracker", toolCallId);
}

async function runClawGetLockupPack(ctx, params, toolCallId) {
  return runPack(ctx, params, TOOL_NAMES.clawGetLockupPack, "lockup_watcher", toolCallId);
}

function registerFrontlineTool(api, name, description, execute, parameters = PACK_INPUT_SCHEMA) {
  api.registerTool(
    (ctx) => ({
      name,
      label: name,
      description,
      parameters,
      async execute(_id, params) {
        return execute(ctx, params, _id);
      },
    }),
    { name, optional: true },
  );
}

export default definePluginEntry({
  id: "claw-trade-frontline-tools",
  name: "claw-trade frontline tools",
  description: "Registers claw-trade frontline data pack tools.",
  register(api) {
    if (typeof api.on === "function") {
      api.on("reply_dispatch", handleReplyDispatchHook);
    }
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetMarketPack,
      "Load one market data pack through the canonical claw-trade data layer.",
      runClawGetMarketPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetFundamentalPack,
      "Load one fundamental data pack through the canonical claw-trade data layer.",
      runClawGetFundamentalPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetNewsPack,
      "Load one news data pack through the canonical claw-trade data layer.",
      runClawGetNewsPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetSocialPack,
      "Load one social data pack through the canonical claw-trade data layer.",
      runClawGetSocialPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetPolicyPack,
      "Load one policy data pack through the canonical claw-trade data layer.",
      runClawGetPolicyPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetHotMoneyPack,
      "Load one hot-money data pack through the canonical claw-trade data layer.",
      runClawGetHotMoneyPack,
      DATA_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetLockupPack,
      "Load one lockup data pack through the canonical claw-trade data layer.",
      runClawGetLockupPack,
      DATA_PACK_INPUT_SCHEMA,
    );
  },
});
