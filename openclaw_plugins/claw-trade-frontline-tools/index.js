import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { definePluginEntry } from "../../third_party/openclaw/dist/plugin-sdk/plugin-entry.js";

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(PLUGIN_DIR, "..", "..");
const TOOL_NAMES = Object.freeze({
  market: "market_market_data_pack",
  fundamental: "fundamental_fundamentals_data_pack",
  news: "news_news_data_pack",
  social: "social_social_sentiment_pack",
});
const FRONTLINE_STAGE = "frontline";
const FRONTLINE_MARKET = "CN_A";
const TOOL_ERROR_CODES = Object.freeze({
  runtimeContextMissing: "TOOL_RUNTIME_CONTEXT_MISSING",
  paramsInvalid: "TOOL_PARAMS_INVALID",
  workerMismatch: "TOOL_WORKER_MISMATCH",
  contextIncomplete: "TOOL_CONTEXT_INCOMPLETE",
  subprocessTimeout: "TOOL_SUBPROCESS_TIMEOUT",
  protocolError: "TOOL_PROTOCOL_ERROR",
});
const DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS = 30000;
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

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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
  if (workerId !== expectedWorkerId) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.workerMismatch,
      `${toolName} worker mismatch: expected ${expectedWorkerId}, got ${workerId ?? "<empty>"}`,
      { tool_name: toolName, expected_worker_id: expectedWorkerId, worker_id: workerId ?? null },
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

function buildRuntimeContext(runtime, toolName, toolCallId) {
  const currentDate = textValue(runtime.runtimeVars.current_date);
  return {
    run_id: runtime.runId,
    stage: runtime.stage,
    worker_id: runtime.workerId,
    call_id: runtime.callId,
    dispatch_id: runtime.callId,
    tool_call_id: textValue(toolCallId) || null,
    tool_name: toolName,
    evidence_root: path.join(runtime.evidenceDir, "pack-tool-evidence"),
    current_date: currentDate,
    current_time: new Date().toISOString(),
  };
}

function assertFrontlineMarket(toolName, toolInput) {
  const market = readOptionalString(toolInput, "market");
  if (!market) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `params.market is required`,
      { tool_name: toolName },
    );
  }
  if (market !== FRONTLINE_MARKET) {
    throw new FrontlineToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `params.market must be ${FRONTLINE_MARKET}, got ${market}`,
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
    const readerBrief = textValue(payload.reader_brief);
    if (!isError && readerBrief) {
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

function marketScriptConfig() {
  const scriptsDir = path.join(
    REPO_ROOT,
    "agents",
    "market_analyst",
    "skills",
    "cn-a-market-data",
    "scripts",
  );
  return {
    expectedWorkerId: "market_analyst",
    requiredFields: ["ticker", "market"],
    args: [
      "-c",
      [
        "import json, sys",
        "from pathlib import Path",
        "scripts_dir = Path(sys.argv[1]).resolve()",
        "sys.path.insert(0, str(scripts_dir))",
        "from market_data_pack import run_market_data_pack",
        "payload = json.load(sys.stdin)",
        "result = run_market_data_pack(payload['tool_input'], payload['runtime_context'])",
        "print(json.dumps(result, ensure_ascii=False, default=str))",
      ].join("; "),
      scriptsDir,
    ],
    pythonPathDirs: [scriptsDir],
    totalTimeoutMs: positiveIntegerEnv("CN_A_PROVIDER_TOTAL_TIMEOUT_MS", DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS),
  };
}

function fundamentalScriptConfig() {
  const scriptsDir = path.join(
    REPO_ROOT,
    "agents",
    "fundamental_analyst",
    "skills",
    "cn-a-fundamental-data",
    "scripts",
  );
  return {
    expectedWorkerId: "fundamental_analyst",
    requiredFields: ["ticker", "market"],
    args: [
      "-c",
      [
        "import json, sys",
        "from pathlib import Path",
        "scripts_dir = Path(sys.argv[1]).resolve()",
        "sys.path.insert(0, str(scripts_dir))",
        "from fundamental_data_pack import tool_entrypoint",
        "payload = json.load(sys.stdin)",
        "result = tool_entrypoint(payload['tool_input'], payload['runtime_context'])",
        "print(json.dumps(result, ensure_ascii=False, default=str))",
      ].join("; "),
      scriptsDir,
    ],
    pythonPathDirs: [scriptsDir],
    totalTimeoutMs: positiveIntegerEnv("CN_A_PROVIDER_TOTAL_TIMEOUT_MS", DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS),
  };
}

function newsScriptConfig() {
  const pythonDir = path.join(
    REPO_ROOT,
    "openclaw_plugins",
    "claw-trade-frontline-tools",
    "python",
  );
  return {
    expectedWorkerId: "news_analyst",
    requiredFields: ["ticker", "market"],
    args: [
      "-c",
      [
        "import json, sys",
        "from pathlib import Path",
        "python_dir = Path(sys.argv[1]).resolve()",
        "sys.path.insert(0, str(python_dir))",
        "from frontline_data_pack.models import to_jsonable",
        "from frontline_data_pack.news_data_pack import run_news_data_pack",
        "payload = json.load(sys.stdin)",
        "result = run_news_data_pack(payload['tool_input'], payload['runtime_context'])",
        "print(json.dumps(to_jsonable(result), ensure_ascii=False, default=str))",
      ].join("; "),
      pythonDir,
    ],
    pythonPathDirs: [pythonDir],
    totalTimeoutMs: domainToolTimeoutMs(
      positiveSecondsEnvToMs("CN_A_NEWS_TOTAL_TIMEOUT_SECONDS", DEFAULT_NEWS_TOTAL_TIMEOUT_MS),
    ),
  };
}

function socialScriptConfig() {
  const pythonDir = path.join(
    REPO_ROOT,
    "openclaw_plugins",
    "claw-trade-frontline-tools",
    "python",
  );
  return {
    expectedWorkerId: "social_analyst",
    requiredFields: ["ticker", "market"],
    args: [
      "-c",
      [
        "import json, sys",
        "from pathlib import Path",
        "python_dir = Path(sys.argv[1]).resolve()",
        "sys.path.insert(0, str(python_dir))",
        "from frontline_data_pack.models import to_jsonable",
        "from frontline_data_pack.social_sentiment_pack import run_social_sentiment_pack",
        "payload = json.load(sys.stdin)",
        "result = run_social_sentiment_pack(payload['tool_input'], payload['runtime_context'])",
        "print(json.dumps(to_jsonable(result), ensure_ascii=False, default=str))",
      ].join("; "),
      pythonDir,
    ],
    pythonPathDirs: [pythonDir],
    totalTimeoutMs: domainToolTimeoutMs(
      positiveSecondsEnvToMs("CN_A_SOCIAL_PACK_TIMEOUT_SECONDS", DEFAULT_SOCIAL_PACK_TIMEOUT_MS),
    ),
  };
}

const TOOL_CONFIG_FACTORIES = Object.freeze({
  [TOOL_NAMES.market]: marketScriptConfig,
  [TOOL_NAMES.fundamental]: fundamentalScriptConfig,
  [TOOL_NAMES.news]: newsScriptConfig,
  [TOOL_NAMES.social]: socialScriptConfig,
});

async function executeFrontlineTool(ctx, params, toolName, toolCallId) {
  const configFactory = TOOL_CONFIG_FACTORIES[toolName];
  if (!configFactory) {
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, `unknown tool config: ${toolName}`, { tool_name: toolName });
  }
  const config = configFactory();
  const runtime = readCommand(ctx, config.expectedWorkerId, toolName);
  const toolInput = buildToolInput(runtime.runtimeVars, params, config.requiredFields);
  assertFrontlineMarket(toolName, toolInput);
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
  return toolResult(result.parsed, false);
}

async function runMarketPack(ctx, params, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, TOOL_NAMES.market, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(TOOL_NAMES.market, "market_analyst", error);
  }
}

async function runFundamentalPack(ctx, params, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, TOOL_NAMES.fundamental, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(TOOL_NAMES.fundamental, "fundamental_analyst", error);
  }
}

async function runNewsPack(ctx, params, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, TOOL_NAMES.news, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(TOOL_NAMES.news, "news_analyst", error);
  }
}

async function runSocialPack(ctx, params, toolCallId) {
  try {
    return await executeFrontlineTool(ctx, params, TOOL_NAMES.social, toolCallId);
  } catch (error) {
    return runtimeErrorToResult(TOOL_NAMES.social, "social_analyst", error);
  }
}

function registerFrontlineTool(api, name, description, execute) {
  api.registerTool(
    (ctx) => ({
      name,
      label: name,
      description,
      parameters: PACK_INPUT_SCHEMA,
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
      TOOL_NAMES.market,
      "Load one structured market data package with price rows, indicators, and chart refs.",
      runMarketPack,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.fundamental,
      "Load one structured fundamentals package for the current CN_A ticker.",
      runFundamentalPack,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.news,
      "Load one structured news package covering company and macro context.",
      runNewsPack,
    );
    registerFrontlineTool(
      api,
      TOOL_NAMES.social,
      "Load one structured social sentiment package for the current ticker.",
      runSocialPack,
    );
  },
});
