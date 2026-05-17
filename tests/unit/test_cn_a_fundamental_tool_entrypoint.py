from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_tool_entrypoint_returns_pack_dict_without_overwriting_quality_status() -> None:
    captured: dict[str, object] = {}
    original_loader = FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime

    def fake_run(payload, runtime_context):
        captured.update({"payload": payload, "runtime_context": runtime_context})
        return {
            "ok": True,
            "quality": {"status": "failed"},
            "profile": {"ticker": payload["ticker"]},
        }

    FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime = lambda: (fake_run, lambda value: value, ValueError)
    try:
        payload = {
            "ticker": "600519",
            "market": "CN_A",
            "start_date": "2026-01-01",
            "end_date": "2026-05-01",
        }
        runtime_context = {
            "current_date": "2026-05-07",
            "run_id": "run-ep-1",
            "dispatch_id": "dispatch-ep-1",
            "worker_id": "fundamental_analyst",
        }
        result = FUNDAMENTAL_PACK_MODULE.tool_entrypoint(payload, runtime_context)
        assert result["quality"]["status"] == "failed"
        assert captured["payload"] == payload
        assert captured["runtime_context"]["worker_id"] == "fundamental_analyst"
        assert captured["runtime_context"]["run_id"] == "run-ep-1"
        assert captured["runtime_context"]["call_id"] == "dispatch-ep-1"
    finally:
        FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime = original_loader


def test_tool_entrypoint_accepts_attribute_objects_and_requires_fields() -> None:
    class _Payload:
        ticker = "600519"
        market = "CN_A"
        start_date = None
        end_date = None

    class _Runtime:
        current_date = "2026-05-07"
        run_id = "run-ep-2"
        dispatch_id = "dispatch-ep-2"
        worker_id = "fundamental_analyst"

    original_loader = FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime

    class _ValidationError(Exception):
        code = FUNDAMENTAL_PACK_MODULE.FND_TOOL_ENTRYPOINT_INVALID_INPUT
        message = "missing ticker"

    def fake_run(payload, runtime_context):
        _ = runtime_context
        ticker = getattr(payload, "ticker", None)
        if ticker is None and isinstance(payload, dict):
            ticker = payload.get("ticker")
        if not ticker:
            raise _ValidationError("missing ticker")
        return {"ok": True, "ticker": ticker}

    FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime = lambda: (fake_run, lambda value: value, _ValidationError)
    try:
        result = FUNDAMENTAL_PACK_MODULE.tool_entrypoint(_Payload(), _Runtime())
        assert result["ticker"] == "600519"

        error = FUNDAMENTAL_PACK_MODULE.tool_entrypoint({}, _Runtime())
        assert error["ok"] is False
        assert error["error"]["code"] == FUNDAMENTAL_PACK_MODULE.FND_TOOL_ENTRYPOINT_INVALID_INPUT
    finally:
        FUNDAMENTAL_PACK_MODULE._load_frontline_pack_runtime = original_loader


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}_entrypoint", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}_entrypoint"] = module
    spec.loader.exec_module(module)
    return module


FUNDAMENTAL_PACK_MODULE = _load_script_module("fundamental_data_pack")
