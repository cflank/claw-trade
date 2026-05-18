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
});
const FRONTLINE_STAGE = "frontline";
const OPENBB_PACK_DOMAIN_MARKET = "market";
const OPENBB_PACK_DOMAIN_FUNDAMENTAL = "fundamental";
const OPENBB_PACK_DOMAIN_NEWS = "news";
const OPENBB_PACK_DOMAIN_SOCIAL = "social";
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
const DEFAULT_CRYPTO_MARKET_PACK_TIMEOUT_MS = 120000;
const DEFAULT_NEWS_TOTAL_TIMEOUT_MS = 20000;
const DEFAULT_SOCIAL_PACK_TIMEOUT_MS = 20000;
const DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS = 25000;
const SUBPROCESS_TIMEOUT_BUFFER_MS = 5000;
const STDERR_SUMMARY_MAX_CHARS = 2000;
const STDOUT_SUMMARY_MAX_CHARS = 2000;

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

const OPENBB_PACK_INPUT_SCHEMA = {
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

function safeToken(value, fallback = "unknown") {
  const raw = textValue(value) ?? fallback;
  const safe = raw.replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 96);
  return safe || fallback;
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

function buildOpenbbPackToolInput(runtimeVars, params) {
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
  const workerCallId = runtime.callId;
  const providerCallId = packEvidenceCallId(workerCallId, toolCallId);
  return {
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

function positiveIntegerEnv(name, fallback) {
  const raw = textValue(process.env[name]);
  if (!raw) {
    return fallback;
  }
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function positiveSecondsEnvToMs(name, fallbackMs) {
  const fallbackSeconds = Math.ceil(fallbackMs / 1000);
  const seconds = positiveIntegerEnv(name, fallbackSeconds);
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
    const readerBrief = textValue(payload.reader_brief) ?? textValue(payload.reader_brief_md);
    if (!isError && readerBrief) {
      const readiness = isRecord(payload.readiness) ? textValue(payload.readiness.status) : undefined;
      if (payload.ok === false || (readiness && readiness !== "ready")) {
        const statusText = readiness ? `资料就绪状态为 ${readiness}` : "资料未标记为可用";
        return [
          `资料包工具已返回，但${statusText}；这只证明工具调用完成，不证明资料覆盖完成。`,
          `请只按下方摘要写已取得事实和缺口，不要补写未提供的数据。`,
          readerBrief,
        ].join("\n");
      }
      return readerBrief;
    }
    const error = isRecord(payload.error) ? payload.error : undefined;
    if (error) {
      const code = textValue(error.code) ?? "UNKNOWN_ERROR";
      const message = textValue(error.message) ?? "工具返回失败，但未提供可读错误说明";
      return `资料包工具失败：${code}。${message}`;
    }
    if (!isError && payload.ok === true) {
      return "资料包工具已返回，但未提供自然语言摘要。请只基于可见事实写证据缺口，不要补写未提供的数据。";
    }
  }
  return isError
    ? "资料包工具失败。请在报告中说明工具失败和证据缺口，不要补写未提供的数据。"
    : "资料包工具已返回。请只基于自然语言摘要中的事实写报告，不要补写未提供的数据。";
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

function openbbPackScriptConfig(expectedWorkerId, packDomain) {
  return {
    expectedWorkerId,
    expectedMarket: ALL_MARKETS,
    args: [
      "-c",
      [
        "import json, os, sys",
        "from pathlib import Path",
        "from urllib.parse import urlparse",
        "try:",
        "    from pymongo import MongoClient",
        "except Exception as exc:",
        "    print(json.dumps({'ok': False, 'error': {'code': 'pack_runtime_blocked', 'message': f'pymongo unavailable: {exc}'}}, ensure_ascii=False))",
        "    raise SystemExit(0)",
        "from claw_trade.data_gateway.errors import DataGatewayError",
        "from claw_trade.data_gateway.mcp.runtime_wrapper import OpenBBRuntimeWrapper, PackToolInput",
        "from claw_trade.data_gateway.models import GatewaySettings, PackDomain",
        "from claw_trade.data_gateway.packs.service import DomainPackService",
        "from claw_trade.data_gateway.providers.defaults import default_provider_config_version, load_default_system_capabilities",
        "from claw_trade.data_gateway.providers.defaults import build_default_provider_adapters",
        "from claw_trade.data_gateway.store import OPENBB_RUN_PROVIDER_PLANS, MongoRunProviderPlanStore, ensure_openbb_store_indexes",
        "payload = json.load(sys.stdin)",
        "tool_input = dict(payload.get('tool_input') or {})",
        "runtime_context = dict(payload.get('runtime_context') or {})",
        "tool_input.setdefault('run_id', runtime_context.get('run_id') or '')",
        "tool_input.setdefault('call_id', runtime_context.get('call_id') or '')",
        "tool_input.setdefault('worker_id', runtime_context.get('worker_id') or '')",
        "tool_input.setdefault('start_date', runtime_context.get('start_date') or '')",
        "tool_input.setdefault('end_date', runtime_context.get('end_date') or '')",
        "tool_input.setdefault('current_date', runtime_context.get('current_date') or '')",
        "evidence_root = str(runtime_context.get('evidence_root') or '').strip()",
        "default_object_store_uri = (Path(evidence_root) / 'techlab' / 'charts-local').resolve().as_uri() if evidence_root else Path('.runtime/dev-services/openbb-evidence').resolve().as_uri()",
        "mongo_uri = (os.environ.get('DATA_GATEWAY_MONGODB_URI') or '').strip() or (os.environ.get('CN_A_MONGODB_URI') or '').strip()",
        "if not mongo_uri:",
        "    print(json.dumps({'ok': False, 'error': {'code': 'pack_runtime_blocked', 'message': 'DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI is not configured'}}, ensure_ascii=False))",
        "    raise SystemExit(0)",
        "db_name = (os.environ.get('DATA_GATEWAY_MONGODB_DATABASE') or '').strip()",
        "if not db_name:",
        "    parsed = urlparse(mongo_uri)",
        "    path_name = parsed.path.strip('/')",
        "    db_name = path_name.split('/', 1)[0] if path_name else 'claw_trade_openbb'",
        "capabilities = load_default_system_capabilities()",
        "provider_config_version = default_provider_config_version(capabilities)",
        "allowed_domains_raw = (os.environ.get('DATA_GATEWAY_ALLOWED_DECLARATIVE_PROVIDER_DOMAINS') or 'example.com')",
        "allowed_domains = tuple(item.strip() for item in allowed_domains_raw.split(',') if item.strip())",
        "settings = GatewaySettings(",
        "    openbb_runtime_url=(os.environ.get('OPENBB_RUNTIME_URL') or 'http://127.0.0.1:8001').strip(),",
        "    openbb_home=(os.environ.get('OPENBB_HOME') or '.runtime/dev-services/openbb').strip(),",
        "    mongo_uri=mongo_uri,",
        "    provider_config_version=provider_config_version,",
        "    provider_catalog_path=(os.environ.get('DATA_GATEWAY_PROVIDER_CATALOG_PATH') or '.runtime/dev-services/openbb/provider-catalog.json').strip(),",
        "    provider_catalog={},",
        "    provider_settings={},",
        "    secret_store_uri=(os.environ.get('DATA_GATEWAY_SECRET_STORE_URI') or 'env://').strip(),",
        "    object_store_uri=(os.environ.get('DATA_GATEWAY_OBJECT_STORE_URI') or default_object_store_uri).strip(),",
        "    single_flight_lease_seconds=int((os.environ.get('DATA_GATEWAY_SINGLE_FLIGHT_LEASE_SECONDS') or '60').strip()),",
        "    raw_payload_inline_max_bytes=int((os.environ.get('DATA_GATEWAY_RAW_PAYLOAD_INLINE_MAX_BYTES') or '4096').strip()),",
        "    allowed_declarative_provider_domains=allowed_domains,",
        ")",
        "client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)",
        "db = client[db_name]",
        "ensure_openbb_store_indexes(db)",
        "plan_store = MongoRunProviderPlanStore(db[OPENBB_RUN_PROVIDER_PLANS])",
        "adapters = build_default_provider_adapters(provider_config_version=provider_config_version)",
        "pack_service = DomainPackService(settings=settings, adapters=adapters)",
        "wrapper = OpenBBRuntimeWrapper(",
        "    settings=settings,",
        "    adapters=adapters,",
        "    pack_service=pack_service,",
        "    run_provider_plan_store=plan_store,",
        ")",
        "try:",
        "    result = wrapper.get_pack(PackDomain(sys.argv[1]), PackToolInput.from_payload(tool_input))",
        "except DataGatewayError as exc:",
        "    print(json.dumps({'ok': False, 'error': {'code': exc.code.value, 'message': exc.root_cause}}, ensure_ascii=False, default=str))",
        "    raise SystemExit(0)",
        "except Exception as exc:",
        "    print(json.dumps({'ok': False, 'error': {'code': 'pack_runtime_blocked', 'message': str(exc)}}, ensure_ascii=False, default=str))",
        "    raise SystemExit(0)",
        "print(json.dumps({'ok': True, 'status': result.readiness.status.value, 'readiness': {'status': result.readiness.status.value}, 'reader_brief': result.reader_brief_md}, ensure_ascii=False, default=str))",
      ].join("\n"),
      packDomain,
    ],
    pythonPathDirs: [path.join(REPO_ROOT, "src")],
    inputBuilder: buildOpenbbPackToolInput,
    totalTimeoutMs: domainToolTimeoutMs(providerTotalTimeoutMs()),
    packDomain,
  };
}

const TOOL_CONFIG_FACTORIES = Object.freeze({
  [TOOL_NAMES.clawGetMarketPack]: () => openbbPackScriptConfig("market_analyst", OPENBB_PACK_DOMAIN_MARKET),
  [TOOL_NAMES.clawGetFundamentalPack]: () => openbbPackScriptConfig("fundamental_analyst", OPENBB_PACK_DOMAIN_FUNDAMENTAL),
  [TOOL_NAMES.clawGetNewsPack]: () => openbbPackScriptConfig("news_analyst", OPENBB_PACK_DOMAIN_NEWS),
  [TOOL_NAMES.clawGetSocialPack]: () => openbbPackScriptConfig("social_analyst", OPENBB_PACK_DOMAIN_SOCIAL),
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
  return toolResult(result.parsed, false);
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
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetMarketPack,
      "Load one market pack from the canonical OpenBB gateway/runtime contract.",
      runClawGetMarketPack,
      OPENBB_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetFundamentalPack,
      "Load one fundamental pack from the canonical OpenBB gateway/runtime contract.",
      runClawGetFundamentalPack,
      OPENBB_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetNewsPack,
      "Load one news pack from the canonical OpenBB gateway/runtime contract.",
      runClawGetNewsPack,
      OPENBB_PACK_INPUT_SCHEMA,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.clawGetSocialPack,
      "Load one social pack from the canonical OpenBB gateway/runtime contract.",
      runClawGetSocialPack,
      OPENBB_PACK_INPUT_SCHEMA,
    );
  },
});
