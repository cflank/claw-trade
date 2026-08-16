import crypto from "node:crypto";
import { definePluginEntry } from "../../third_party/openclaw/dist/plugin-sdk/plugin-entry.js";

const TOOL_NAME = "claw-trade-scheduled-work-wake";
const DEFAULT_TIMEOUT_MS = 30000;
const TOOL_INPUT_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  required: ["kind", "cronRunId"],
  properties: {
    kind: { type: "string", enum: ["price_alert_scan", "scheduled_report", "scheduled_selection", "selection_data_refresh", "data_maintenance"] },
    cronRunId: { type: "string", minLength: 1 },
    requestId: { type: "string", minLength: 1 },
    bucketKey: { type: "string", minLength: 1 },
    scheduledReportId: { type: "string", minLength: 1 },
    market: { type: "string", minLength: 1 },
    jobKind: { type: "string", minLength: 1 },
    reason: { type: "string", minLength: 1 },
    maintenanceJobId: { type: "string", minLength: 1 },
  },
});

const TOOL_ERROR_CODES = Object.freeze({
  paramsInvalid: "TOOL_PARAMS_INVALID",
  configMissing: "TOOL_CONFIG_MISSING",
  httpFailed: "TOOL_HTTP_FAILED",
  protocolError: "TOOL_PROTOCOL_ERROR",
});

class ScheduledWorkToolError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function toolResult(payload, isError = false) {
  return {
    isError,
    content: [{ type: "text", text: modelFacingToolText(payload, isError) }],
    details: payload,
  };
}

function toolErrorResult(code, auditMessage, details = undefined) {
  return toolResult(
    {
      ok: false,
      error: {
        code,
        message: safeModelErrorMessage(code),
        audit_message: auditMessage,
        ...(isRecord(details) ? details : {}),
      },
    },
    true,
  );
}

function modelFacingToolText(payload, isError = false) {
  if (isRecord(payload) && payload.status === "ok") {
    return "内部定时任务入口已唤醒。";
  }
  if (isRecord(payload) && payload.status === "error") {
    return "内部定时任务入口返回失败状态。";
  }
  return isError ? "内部定时任务唤醒工具失败。" : "内部定时任务唤醒工具完成。";
}

function safeModelErrorMessage(code) {
  switch (code) {
    case TOOL_ERROR_CODES.paramsInvalid:
      return "工具参数不符合定时任务唤醒合同";
    case TOOL_ERROR_CODES.configMissing:
      return "内部定时任务入口未配置";
    case TOOL_ERROR_CODES.httpFailed:
      return "内部定时任务入口调用失败";
    default:
      return "定时任务唤醒工具失败";
  }
}

function validateParams(params) {
  if (!isRecord(params)) {
    throw new Error("tool params must be a JSON object");
  }
  const kind = textValue(params.kind);
  const allowedByKind = {
    price_alert_scan: ["bucketKey", "cronRunId", "kind", "requestId"],
    scheduled_report: ["cronRunId", "kind", "requestId", "scheduledReportId"],
    scheduled_selection: ["cronRunId", "kind", "requestId", "scheduledReportId"],
    selection_data_refresh: ["cronRunId", "kind", "reason", "requestId"],
    data_maintenance: ["cronRunId", "jobKind", "kind", "maintenanceJobId", "market", "requestId"],
  };
  const allowed = allowedByKind[kind];
  if (!allowed) {
    throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, "unsupported scheduled work kind");
  }
  const keys = Object.keys(params).sort();
  for (const key of keys) {
    if (!allowed.includes(key)) {
      throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, `unsupported ${kind} tool param: ${key}`);
    }
  }
  const cronRunId = textValue(params.cronRunId);
  if (!cronRunId) {
    throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, `${kind} requires cronRunId`);
  }
  const requestId = textValue(params.requestId);
  const base = {
    kind,
    ...(requestId ? { requestId } : {}),
  };
  const withCronRunId = (payload, fallbackKey = undefined) => ({
    ...payload,
    cronRunId: cronRunId === "auto" ? buildCronRunId(kind, fallbackKey) : cronRunId,
  });

  if (kind === "price_alert_scan") {
    const bucketKey = textValue(params.bucketKey);
    if (!bucketKey) {
      throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, "price_alert_scan requires bucketKey");
    }
    return withCronRunId({ ...base, bucketKey }, bucketKey);
  }

  if (kind === "scheduled_report" || kind === "scheduled_selection") {
    const scheduledReportId = textValue(params.scheduledReportId);
    if (!scheduledReportId) {
      throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, `${kind} requires scheduledReportId`);
    }
    return withCronRunId({ ...base, scheduledReportId }, scheduledReportId);
  }

  if (kind === "selection_data_refresh") {
    const reason = textValue(params.reason) ?? "scheduled_data_refresh";
    return withCronRunId({ ...base, reason }, reason);
  }

  const market = textValue(params.market);
  const jobKind = textValue(params.jobKind);
  if (!market || !jobKind) {
    throw new ScheduledWorkToolError(TOOL_ERROR_CODES.paramsInvalid, "data_maintenance requires market and jobKind");
  }
  const maintenanceJobId = textValue(params.maintenanceJobId);
  return withCronRunId(
    {
      ...base,
      market,
      jobKind,
      ...(maintenanceJobId ? { maintenanceJobId } : {}),
    },
    `${market}:${jobKind}`,
  );
}

function safeCronRunIdPart(value) {
  return String(value ?? "cron").replace(/[^A-Za-z0-9_.:-]+/g, "_");
}

function buildCronRunId(kind, key) {
  const prefixByKind = {
    price_alert_scan: "price-alert-scan",
    scheduled_report: "scheduled-report",
    scheduled_selection: "scheduled-selection",
    selection_data_refresh: "selection-data-refresh",
    data_maintenance: "data-maintenance",
  };
  const prefix = prefixByKind[kind] ?? safeCronRunIdPart(kind);
  const safeKey = safeCronRunIdPart(key);
  return `${prefix}:${safeKey}:${Date.now()}:${crypto.randomUUID()}`;
}

function internalWakeConfig() {
  const baseUrl = textValue(process.env.CLAW_TRADE_UI_INTERNAL_BASE_URL) ?? textValue(process.env.CLAW_TRADE_UI_INBOUND_URL);
  const token = textValue(process.env.CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN);
  if (!baseUrl || !token) {
    throw new ScheduledWorkToolError(
      TOOL_ERROR_CODES.configMissing,
      "CLAW_TRADE_UI_INTERNAL_BASE_URL/CLAW_TRADE_UI_INBOUND_URL and CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN are required",
    );
  }
  const url = resolveInternalWakeUrl(baseUrl);
  return { url, token };
}

function resolveInternalWakeUrl(baseUrl) {
  const parsed = new URL(baseUrl);
  let basePath = parsed.pathname || "/";
  if (basePath.endsWith("/channel-inbound-message")) {
    basePath = basePath.slice(0, -"/channel-inbound-message".length);
  }
  if (basePath === "/" || basePath === "") {
    basePath = "/api/ui";
  }
  if (!basePath.endsWith("/")) {
    basePath += "/";
  }
  return new URL("internal/scheduled-work/cron-wake", `${parsed.origin}${basePath}`);
}

async function executeWakeTool(params) {
  let input;
  let config;
  try {
    input = validateParams(params);
    config = internalWakeConfig();
  } catch (error) {
    const code = error instanceof ScheduledWorkToolError ? error.code : TOOL_ERROR_CODES.paramsInvalid;
    return toolErrorResult(code, error instanceof Error ? error.message : String(error));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  timer.unref?.();
  let response;
  try {
    response = await fetch(config.url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-claw-trade-internal-token": config.token,
      },
      body: JSON.stringify(input),
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    return toolErrorResult(TOOL_ERROR_CODES.httpFailed, error instanceof Error ? error.message : String(error));
  }
  clearTimeout(timer);
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, error instanceof Error ? error.message : String(error), {
      status: response.status,
    });
  }
  if (!response.ok) {
    return toolErrorResult(TOOL_ERROR_CODES.httpFailed, `HTTP ${response.status}`, { status: response.status, response: payload });
  }
  return toolResult(payload, isRecord(payload) && payload.status === "error");
}

function registerScheduledWorkTool(api) {
  api.registerTool(
    () => ({
      name: TOOL_NAME,
      label: TOOL_NAME,
      description: "Wake the claw-trade internal scheduled-work route for a cron-provided payload.",
      parameters: TOOL_INPUT_SCHEMA,
      async execute(_callId, params) {
        return executeWakeTool(params);
      },
    }),
    { name: TOOL_NAME, optional: true },
  );
}

export default definePluginEntry({
  id: "claw-trade-scheduled-work-tools",
  name: "claw-trade scheduled work tools",
  description: "Registers internal scheduled-work wake tool.",
  register(api) {
    registerScheduledWorkTool(api);
  },
});
