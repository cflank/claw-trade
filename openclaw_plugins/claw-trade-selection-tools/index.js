import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { definePluginEntry } from "../../third_party/openclaw/dist/plugin-sdk/plugin-entry.js";

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(PLUGIN_DIR, "..", "..");

const TOOL_NAME = "claw_get_selection_candidate_cache";
const REQUIRED_STAGE = "selection_review";
const ALLOWED_WORKERS = new Set(["selection_strategist", "selection_skeptic"]);
const DEFAULT_TIMEOUT_MS = 20000;
const SUBPROCESS_TIMEOUT_BUFFER_MS = 5000;

const TOOL_ERROR_CODES = Object.freeze({
  runtimeContextMissing: "TOOL_RUNTIME_CONTEXT_MISSING",
  paramsInvalid: "TOOL_PARAMS_INVALID",
  contextIncomplete: "TOOL_CONTEXT_INCOMPLETE",
  workerMismatch: "TOOL_WORKER_MISMATCH",
  subprocessTimeout: "TOOL_SUBPROCESS_TIMEOUT",
  protocolError: "TOOL_PROTOCOL_ERROR",
});

const EMPTY_PARAMS_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  properties: {},
});

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function absolutePathValue(value) {
  const text = textValue(value);
  if (!text) {
    return undefined;
  }
  return path.isAbsolute(text) ? text : path.resolve(text);
}

class SelectionToolError extends Error {
  constructor(code, message, details = undefined) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

function toolResult(payload, isError = false) {
  return {
    isError,
    content: [{ type: "text", text: modelFacingToolText(payload, isError) }],
    details: payload,
  };
}

function toolErrorResult(code, message, details = undefined) {
  return toolResult(
    {
      ok: false,
      error: {
        code,
        message: safeModelErrorMessage(code),
        audit_message: message,
        ...(isRecord(details) ? details : {}),
      },
    },
    true,
  );
}

function modelFacingToolText(payload, isError = false) {
  if (isRecord(payload)) {
    const readerBrief = textValue(payload.reader_brief_md) ?? textValue(payload.reader_brief);
    if (readerBrief) {
      return readerBrief;
    }
    const error = isRecord(payload.error) ? payload.error : undefined;
    if (error) {
      const code = textValue(error.code) ?? "UNKNOWN_ERROR";
      const message = safeModelErrorMessage(code);
      return `候选缓存工具失败：${code}。${message}`;
    }
  }
  return isError ? "候选缓存工具失败。请说明证据缺口，不要补写不存在数据。" : "候选缓存工具未返回可读正文。";
}

function safeModelErrorMessage(code) {
  switch (code) {
    case TOOL_ERROR_CODES.paramsInvalid:
      return "工具参数不符合公开合同";
    case TOOL_ERROR_CODES.contextIncomplete:
      return "选择工具运行上下文不完整";
    case TOOL_ERROR_CODES.workerMismatch:
      return "当前 worker 不能使用候选缓存工具";
    case TOOL_ERROR_CODES.runtimeContextMissing:
      return "选择工具运行上下文缺失";
    case TOOL_ERROR_CODES.subprocessTimeout:
      return "候选缓存工具执行超时";
    case TOOL_ERROR_CODES.protocolError:
      return "候选缓存工具协议执行失败";
    default:
      return "候选缓存工具失败";
  }
}

function readCommand(ctx) {
  const command = ctx?.singleWorkerCommand;
  if (!isRecord(command)) {
    throw new SelectionToolError(
      TOOL_ERROR_CODES.runtimeContextMissing,
      `${TOOL_NAME} requires ctx.singleWorkerCommand`,
      { tool_name: TOOL_NAME },
    );
  }
  const workerId = textValue(command.worker_id);
  const stage = textValue(command.stage);
  const runId = textValue(command.run_id);
  const callId = textValue(command.call_id);
  const evidenceDir = absolutePathValue(command.evidence_dir);
  const runtimeVars = isRecord(command.runtime_vars) ? command.runtime_vars : {};
  const selectWorkflowRunId = textValue(command.select_workflow_run_id) ?? textValue(runtimeVars.select_workflow_run_id);
  const selectionRunId = textValue(command.selection_run_id) ?? textValue(runtimeVars.selection_run_id);

  if (!workerId || !stage || !runId || !callId || !evidenceDir || !selectWorkflowRunId || !selectionRunId) {
    throw new SelectionToolError(
      TOOL_ERROR_CODES.contextIncomplete,
      `${TOOL_NAME} runtime context is incomplete`,
      { tool_name: TOOL_NAME },
    );
  }
  if (!ALLOWED_WORKERS.has(workerId)) {
    throw new SelectionToolError(
      TOOL_ERROR_CODES.workerMismatch,
      `${TOOL_NAME} worker mismatch: ${workerId}`,
      { tool_name: TOOL_NAME, worker_id: workerId },
    );
  }
  if (stage !== REQUIRED_STAGE) {
    throw new SelectionToolError(
      TOOL_ERROR_CODES.contextIncomplete,
      `${TOOL_NAME} requires stage=${REQUIRED_STAGE}, got ${stage}`,
      { tool_name: TOOL_NAME, stage },
    );
  }
  return {
    command,
    workerId,
    stage,
    runId,
    callId,
    evidenceDir,
    selectWorkflowRunId,
    selectionRunId,
    runtimeVars,
  };
}

function validateEmptyParams(params) {
  if (!isRecord(params)) {
    throw new SelectionToolError(TOOL_ERROR_CODES.paramsInvalid, "tool params must be a JSON object");
  }
  if (Object.keys(params).length > 0) {
    throw new SelectionToolError(
      TOOL_ERROR_CODES.paramsInvalid,
      `${TOOL_NAME} does not accept business params`,
      { fields: Object.keys(params).sort() },
    );
  }
}

function pythonExecutable() {
  const explicit = textValue(process.env.CLAW_TRADE_SELECTION_TOOL_PYTHON);
  if (explicit) {
    return explicit;
  }
  const venvPython = path.join(REPO_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

function parseJsonFromStdout(stdout) {
  const text = String(stdout ?? "").trim();
  if (!text) {
    return undefined;
  }
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function runPythonJson(args, payload, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    function complete(result) {
      if (done) {
        return;
      }
      done = true;
      resolve(result);
    }
    const child = spawn(pythonExecutable(), args, {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: [REPO_ROOT, path.join(REPO_ROOT, "src"), process.env.PYTHONPATH ?? ""]
          .filter(Boolean)
          .join(path.delimiter),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let killed = false;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      complete({ exitCode: 127, stdout, stderr: `${stderr}\n${error.message}`.trim(), parsed: undefined });
    });
    const timer = setTimeout(() => {
      killed = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 2000).unref?.();
    }, timeoutMs);
    timer.unref?.();
    child.on("close", (exitCode) => {
      clearTimeout(timer);
      complete({
        exitCode: killed ? 124 : exitCode ?? 1,
        stdout,
        stderr: killed ? `${stderr}\nprocess timed out after ${timeoutMs}ms`.trim() : stderr,
        parsed: killed ? undefined : parseJsonFromStdout(stdout),
      });
    });
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

function shouldMarkToolResultAsError(payload) {
  if (!isRecord(payload)) {
    return true;
  }
  if (payload.ok === false) {
    return true;
  }
  return isRecord(payload.error);
}

async function runSelectionTool(ctx, params, toolCallId) {
  let runtime;
  try {
    validateEmptyParams(params);
    runtime = readCommand(ctx);
  } catch (error) {
    if (error instanceof SelectionToolError) {
      return toolErrorResult(error.code, error.message, error.details);
    }
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, String(error));
  }

  const payload = {
    tool_input: {},
    runtime_context: {
      tool_name: TOOL_NAME,
      run_id: runtime.runId,
      call_id: runtime.callId,
      dispatch_id: runtime.callId,
      worker_id: runtime.workerId,
      stage: runtime.stage,
      evidence_root: path.join(runtime.evidenceDir, "selection-candidate-cache-tool-evidence"),
      tool_call_id: textValue(toolCallId) ?? null,
      select_workflow_run_id: runtime.selectWorkflowRunId,
      selection_run_id: runtime.selectionRunId,
      runtime_vars: runtime.runtimeVars,
      candidate_cache_ref: isRecord(runtime.command.candidate_cache_ref)
        ? runtime.command.candidate_cache_ref
        : runtime.runtimeVars.candidate_cache_ref,
      selection_artifact_root:
        absolutePathValue(runtime.command.selection_artifact_root) ??
        absolutePathValue(runtime.runtimeVars.selection_artifact_root) ??
        null,
    },
  };

  const timeoutMs = DEFAULT_TIMEOUT_MS + SUBPROCESS_TIMEOUT_BUFFER_MS;
  const result = await runPythonJson(["-m", "claw_trade.selection.tools"], payload, timeoutMs);
  if (result.exitCode === 124) {
    return toolErrorResult(TOOL_ERROR_CODES.subprocessTimeout, `${TOOL_NAME} subprocess timed out`, {
      timeout_ms: timeoutMs,
      stderr: String(result.stderr ?? "").slice(0, 2000),
    });
  }
  if (result.parsed === undefined) {
    return toolErrorResult(TOOL_ERROR_CODES.protocolError, `${TOOL_NAME} subprocess protocol error`, {
      exit_code: result.exitCode,
      stderr: String(result.stderr ?? "").slice(0, 2000),
      stdout: String(result.stdout ?? "").slice(0, 2000),
    });
  }
  return toolResult(result.parsed, shouldMarkToolResultAsError(result.parsed));
}

function registerSelectionTool(api) {
  api.registerTool(
    (ctx) => ({
      name: TOOL_NAME,
      label: TOOL_NAME,
      description: "Read the approved A-share selection candidate cache for current selection review turn.",
      parameters: EMPTY_PARAMS_SCHEMA,
      async execute(callId, params) {
        return runSelectionTool(ctx, params, callId);
      },
    }),
    { name: TOOL_NAME, optional: true },
  );
}

export default definePluginEntry({
  id: "claw-trade-selection-tools",
  name: "claw-trade selection tools",
  description: "Registers selection candidate-cache tool with approved-only runtime boundary.",
  register(api) {
    registerSelectionTool(api);
  },
});
