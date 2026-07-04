import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { definePluginEntry } from "../../third_party/openclaw/dist/plugin-sdk/plugin-entry.js";

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(PLUGIN_DIR, "..", "..");
const TOOL_NAMES = Object.freeze({
  clawRequestData: "claw_request_data",
});
const FRONTLINE_STAGE = "frontline";
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
const DEFAULT_DATA_NEED_TOOL_BUDGET_MS = 180000;
const DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS = 5000;
const SUBPROCESS_TIMEOUT_BUFFER_MS = 5000;
const SUBPROCESS_FORCE_KILL_GRACE_MS = 2000;
const SUBPROCESS_TIMEOUT_CLOSE_GRACE_MS = 1000;
const STDERR_SUMMARY_MAX_CHARS = 2000;
const STDOUT_SUMMARY_MAX_CHARS = 2000;
const WECHAT_CHANNEL_ID = "openclaw-weixin";
const UI_CHANNEL_KIND = "wechat_clawbot";
const DEFAULT_UI_INBOUND_TIMEOUT_MS = 180000;
const IMMEDIATE_INBOUND_ACK_TEXT = "收到，正在处理。";
const REPORT_BRIDGE_FALLBACK_TEXT =
  "报告请求已收到，但当前无法确认处理结果。请稍后查看微信消息；如果没有收到文件或回复，请再发送一次。";

const OPTIONAL_TEXT = {
  type: "string",
};

const DATA_REQUEST_INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["item", "purpose"],
  properties: {
    item: OPTIONAL_TEXT,
    instrument: OPTIONAL_TEXT,
    market: OPTIONAL_TEXT,
    time_range: {
      type: "object",
      additionalProperties: false,
      properties: {
        start: OPTIONAL_TEXT,
        end: OPTIONAL_TEXT,
        lookback_days: {
          type: "integer",
          minimum: 1,
        },
      },
    },
    granularity: OPTIONAL_TEXT,
    purpose: OPTIONAL_TEXT,
    priority: {
      type: "string",
      enum: ["required", "normal", "optional", "expensive"],
    },
  },
};

const DATA_REQUEST_FORBIDDEN_INPUT_KEYS = Object.freeze([
  "provider",
  "path",
  "api_name",
  "url",
  "header",
  "headers",
  "token",
  "api_key",
  "secret",
  "api_id",
  "data_type",
  "fields",
]);
const DATA_REQUEST_ALLOWED_INPUT_KEYS = Object.freeze([
  "item",
  "instrument",
  "market",
  "time_range",
  "granularity",
  "purpose",
  "priority",
]);

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

function normalizeMarketValue(value) {
  const raw = textValue(value);
  if (!raw) {
    return undefined;
  }
  return raw.toUpperCase();
}

function forbiddenDataRequestKeys(value, found = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) {
      forbiddenDataRequestKeys(item, found);
    }
    return found;
  }
  if (!isRecord(value)) {
    return found;
  }
  for (const [key, child] of Object.entries(value)) {
    if (DATA_REQUEST_FORBIDDEN_INPUT_KEYS.includes(key) || key.startsWith("allowed_") || key.startsWith("only_for_")) {
      found.add(key);
    }
    forbiddenDataRequestKeys(child, found);
  }
  return found;
}

function readInboundTimeoutMs() {
  return nonNegativeIntegerValue(process.env.CLAW_TRADE_UI_INBOUND_TIMEOUT_MS) ?? DEFAULT_UI_INBOUND_TIMEOUT_MS;
}

function safeToken(value, defaultValue = "unknown") {
  const raw = textValue(value) ?? defaultValue;
  const safe = raw.replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 96);
  return safe || defaultValue;
}

function dataNeedEvidenceCallId(workerCallId, toolCallId) {
  return `${workerCallId}__tool-${safeToken(toolCallId)}`;
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
  const expectedWorkerIds = Array.isArray(expectedWorkerId)
    ? expectedWorkerId.filter(Boolean)
    : textValue(expectedWorkerId)
      ? [expectedWorkerId]
      : [];
  if (expectedWorkerIds.length > 0 && !expectedWorkerIds.includes(workerId)) {
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

function runtimeOnlyText(runtimeVars, fieldName, required = false) {
  const value = readOptionalRuntimeString(runtimeVars, fieldName);
  if (required && value === undefined) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, `runtime_vars.${fieldName} is required`);
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

function claimReportBridgeFailure(payload, ctx) {
  const text = textValue(payload?.text);
  if (!text || !looksLikeReportRequestText(text)) {
    return null;
  }
  const queuedFinal = Boolean(ctx?.dispatcher?.sendFinalReply?.({ text: REPORT_BRIDGE_FALLBACK_TEXT }));
  return {
    handled: true,
    queuedFinal,
    counts: currentDispatchCounts(ctx?.dispatcher),
  };
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
  const payload = buildUiInboundPayload(event);
  if (!payload) {
    return { handled: false, queuedFinal: false, counts };
  }
  const inboundUrl = textValue(process.env.CLAW_TRADE_UI_INBOUND_URL);
  if (!inboundUrl) {
    return claimReportBridgeFailure(payload, ctx) ?? { handled: false, queuedFinal: false, counts };
  }
  const ackQueued = shouldSendImmediateInboundAck(payload)
    ? Boolean(ctx?.dispatcher?.sendFinalReply?.({ text: IMMEDIATE_INBOUND_ACK_TEXT }))
    : false;
  try {
    const inboundResult = await postInboundMessageToUi(inboundUrl, payload, readInboundTimeoutMs());
    if (!isRecord(inboundResult)) {
      return (
        claimReportBridgeFailure(payload, ctx) ??
        { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) }
      );
    }
    if (inboundResult.handled !== true) {
      return (
        claimReportBridgeFailure(payload, ctx) ??
        { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) }
      );
    }
    const replyText = textValue(inboundResult.replyText);
    if (!replyText) {
      console.warn("[claw-trade-frontline-tools] inbound UI bridge returned handled=true without replyText");
      return (
        claimReportBridgeFailure(payload, ctx) ??
        { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) }
      );
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
    return (
      claimReportBridgeFailure(payload, ctx) ??
      { handled: false, queuedFinal: false, counts: currentDispatchCounts(ctx?.dispatcher) }
    );
  }
}

function buildDataRequestToolInput(runtimeVars, params, toolName) {
  if (!isRecord(params)) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "tool input must be a JSON object");
  }
  const unexpected = Object.keys(params)
    .filter((key) => !DATA_REQUEST_ALLOWED_INPUT_KEYS.includes(key))
    .sort();
  const forbidden = Array.from(forbiddenDataRequestKeys(params)).sort();
  if (forbidden.length > 0) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "data request contains unsupported execution details",
    );
  }
  if (unexpected.length > 0) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "data request contains unsupported fields",
    );
  }
  const runtime = isRecord(runtimeVars) ? runtimeVars : {};
  const item = readOptionalString(params, "item");
  const purpose = readOptionalString(params, "purpose");
  if (!item) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "data request item is required");
  }
  if (!purpose) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "data request purpose is required");
  }
  const market = normalizeMarketValue(readOptionalString(params, "market") ?? runtimeOnlyText(runtime, "market", true));
  const timeRange = normalizeDataNeedTimeRange(params.time_range);
  const input = {
    item,
    instrument: readOptionalString(params, "instrument") ?? runtimeOnlyText(runtime, "ticker", true),
    market,
    time_range: timeRange,
    granularity: readOptionalString(params, "granularity"),
    purpose,
    priority: readOptionalString(params, "priority"),
  };
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined));
}

function normalizeDataNeedTimeRange(value) {
  if (value === undefined || value === null) {
    return undefined;
  }
  if (!isRecord(value)) {
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "data request time_range must be an object");
  }
  const allowed = new Set(["start", "end", "lookback_days"]);
  const unexpected = Object.keys(value).filter((key) => !allowed.has(key)).sort();
  if (unexpected.length > 0) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "data request time_range contains unsupported fields",
    );
  }
  const start = readOptionalString(value, "start");
  const end = readOptionalString(value, "end");
  const out = {};
  if (start) {
    out.start = start;
  }
  if (end) {
    out.end = end;
  }
  if (value.lookback_days !== undefined) {
    const parsed = Number.parseInt(String(value.lookback_days), 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "data request lookback_days must be positive");
    }
    out.lookback_days = parsed;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function buildRuntimeContext(runtime, toolName, toolCallId) {
  const now = new Date();
  const currentDate = textValue(runtime.runtimeVars.current_date);
  const startDate = textValue(runtime.runtimeVars.start_date);
  const endDate = textValue(runtime.runtimeVars.end_date);
  const reportPrefetchManifestPath = textValue(runtime.runtimeVars.report_prefetch_manifest_path);
  const workerCallId = runtime.callId;
  const providerCallId = dataNeedEvidenceCallId(workerCallId, toolCallId);
  const context = {
    run_id: runtime.runId,
    stage: runtime.stage,
    worker_id: runtime.workerId,
    call_id: providerCallId,
    dispatch_id: workerCallId,
    worker_call_id: workerCallId,
    tool_call_id: textValue(toolCallId) || null,
    tool_name: toolName,
    evidence_root: path.join(runtime.evidenceDir, "data-need-tool-evidence"),
    current_date: currentDate,
    start_date: startDate,
    end_date: endDate,
    current_time: now.toISOString(),
    deadline_at: new Date(now.getTime() + dataNeedToolBudgetMs()).toISOString(),
  };
  if (reportPrefetchManifestPath) {
    context.report_prefetch_required = true;
    context.report_prefetch_manifest_path = reportPrefetchManifestPath;
  }
  return context;
}

function assertExpectedMarket(toolName, toolInput, expectedMarket) {
  const market = normalizeMarketValue(toolInput.market);
  if (!market) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "data request market is required",
      { tool_name: toolName },
    );
  }
  const expectedMarkets = Array.isArray(expectedMarket) ? expectedMarket : [expectedMarket];
  if (!expectedMarkets.includes(market)) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      "data request market is outside this worker profile",
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

function dataNeedToolBudgetMs() {
  return positiveSecondsEnvToMs("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS", DEFAULT_DATA_NEED_TOOL_BUDGET_MS);
}

function legacyProviderTotalTimeoutMs() {
  return positiveIntegerEnv("CN_A_PROVIDER_TOTAL_TIMEOUT_MS", DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS);
}

function domainToolTimeoutMs(domainTotalTimeoutMs, dataNeedBudgetMs) {
  const totalTimeout = Number.isFinite(domainTotalTimeoutMs) && domainTotalTimeoutMs > 0
    ? domainTotalTimeoutMs
    : DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS;
  const toolBudget = Number.isFinite(dataNeedBudgetMs) && dataNeedBudgetMs > 0
    ? dataNeedBudgetMs
    : DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS;
  return Math.max(totalTimeout, legacyProviderTotalTimeoutMs(), toolBudget);
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
    let timeoutTimer;
    let forceKillTimer;
    let timeoutFallbackTimer;
    function complete(result) {
      if (completed) {
        return;
      }
      completed = true;
      if (timeoutTimer) {
        clearTimeout(timeoutTimer);
      }
      if (forceKillTimer) {
        clearTimeout(forceKillTimer);
      }
      if (timeoutFallbackTimer) {
        clearTimeout(timeoutFallbackTimer);
      }
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
      detached: process.platform !== "win32",
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
    function timeoutResult() {
      return {
        exitCode: 124,
        stdout,
        stderr: `${stderr}\nprocess timed out after ${timeoutMs}ms`.trim(),
        parsed: undefined,
      };
    }
    timeoutTimer = setTimeout(() => {
      timedOut = true;
      terminateSpawnedProcess(child, "SIGTERM");
      forceKillTimer = setTimeout(() => {
        terminateSpawnedProcess(child, "SIGKILL");
        timeoutFallbackTimer = setTimeout(() => complete(timeoutResult()), SUBPROCESS_TIMEOUT_CLOSE_GRACE_MS);
        timeoutFallbackTimer.unref?.();
      }, SUBPROCESS_FORCE_KILL_GRACE_MS);
      forceKillTimer.unref?.();
    }, timeoutMs);
    timeoutTimer?.unref?.();
    child.on("close", (exitCode) => {
      complete({
        exitCode: timedOut ? 124 : exitCode ?? 1,
        stdout,
        stderr: timedOut ? timeoutResult().stderr : stderr,
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

function terminateSpawnedProcess(child, signal) {
  if (child?.pid && process.platform !== "win32") {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch {
      // Fall back to killing the direct child below.
    }
  }
  try {
    child.kill(signal);
  } catch {
    // The child may already have exited.
  }
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
    details: toolResultDetails(payload, isError),
  };
}

function toolResultDetails(payload, isError = false) {
  if (!isError && isRecord(payload) && typeof payload.status === "string") {
    const { status, ...rest } = payload;
    return {
      ...rest,
      data_result_status: status,
    };
  }
  return payload;
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
      return `${safeModelErrorMessage(textValue(error.code))}；不要补写不存在的数据结果。`;
    }
    if (!isError && payload.ok === true) {
      return "";
    }
  }
  return isError
    ? "数据工具未能完成本次数据请求；不要补写不存在的数据结果。"
    : "";
}

function safeModelErrorMessage(code) {
  switch (code) {
    case TOOL_ERROR_CODES.paramsInvalid:
      return "工具参数不符合公开合同";
    case TOOL_ERROR_CODES.subprocessTimeout:
      return "数据工具执行超时";
    case TOOL_ERROR_CODES.protocolError:
      return "数据工具协议执行失败";
    case "invalid_public_data_request":
      return "数据需求参数不符合公开工具合同";
    case "data_need_runtime_blocked":
      return "数据层运行时未能完成本次数据请求";
    default:
      return "工具返回失败，错误说明见审计字段";
  }
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
      message: safeModelErrorMessage(code),
      audit_message: message,
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
    throw new FrontlineToolError(TOOL_ERROR_CODES.paramsInvalid, "tool input field must be a string");
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

function dataNeedScriptConfig() {
  return {
    expectedMarket: ALL_MARKETS,
    args: [
      "-c",
      [
        "import json, sys",
        "try:",
        "    from claw_trade.reports.data_need_bridge import run_claw_request_data",
        "    payload = json.load(sys.stdin)",
        "    tool_input = dict(payload.get('tool_input') or {})",
        "    runtime_context = dict(payload.get('runtime_context') or {})",
        "    result = run_claw_request_data(tool_input, runtime_context)",
        "except Exception as exc:",
        "    print(json.dumps({'ok': False, 'error': {'code': 'data_need_runtime_blocked', 'message': '数据层运行时未能完成本次数据请求', 'audit_message': str(exc)}, 'model_visible_text': '数据层运行时未能完成本次数据请求；不要补写不存在的数据结果。'}, ensure_ascii=False, default=str))",
        "    raise SystemExit(0)",
        "print(json.dumps(result, ensure_ascii=False, default=str))",
      ].join("\n"),
    ],
    pythonPathDirs: [path.join(REPO_ROOT, "src")],
    inputBuilder: buildDataRequestToolInput,
    totalTimeoutMs: domainToolTimeoutMs(legacyProviderTotalTimeoutMs(), dataNeedToolBudgetMs()),
  };
}

const TOOL_CONFIG_FACTORIES = Object.freeze({
  [TOOL_NAMES.clawRequestData]: () => dataNeedScriptConfig(),
});

async function executeFrontlineTool(ctx, params, toolName, toolCallId) {
  const configFactory = TOOL_CONFIG_FACTORIES[toolName];
  if (!configFactory) {
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, `unknown tool config: ${toolName}`, { tool_name: toolName });
  }
  const config = configFactory();
  const runtime = readCommand(ctx, config.expectedWorkerId, toolName);
  const toolInput = config.inputBuilder(runtime.runtimeVars, params, toolName);
  assertExpectedMarket(toolName, toolInput, config.expectedMarket);
  const runtimeContext = buildRuntimeContext(runtime, toolName, toolCallId);
  const payload = {
    tool_input: toolInput,
    runtime_context: runtimeContext,
  };
  const timeoutMs = subprocessTimeoutMs(config.totalTimeoutMs);
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

async function runFrontlineDataTool(ctx, params, toolName, expectedWorkerId, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, toolName, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(toolName, expectedWorkerId, error);
  }
}

async function runClawRequestData(ctx, params, toolCallId) {
  return runFrontlineDataTool(
    ctx,
    params,
    TOOL_NAMES.clawRequestData,
    undefined,
    toolCallId,
  );
}

function registerFrontlineTool(api, name, description, execute, parameters = DATA_REQUEST_INPUT_SCHEMA) {
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
  description: "Registers the claw-trade frontline data layer tool.",
  register(api) {
    if (typeof api.on === "function") {
      api.on("reply_dispatch", handleReplyDispatchHook);
    }
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawRequestData,
      "Request a business data item through the canonical claw-trade data layer.",
      runClawRequestData,
      DATA_REQUEST_INPUT_SCHEMA,
    );
  },
});
