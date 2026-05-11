from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_tool_entrypoint_returns_pack_dict_without_overwriting_quality_status() -> None:
    captured: dict[str, object] = {}
    original_policy = FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools
    original_build = FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack
    FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools = lambda worker_id, market: captured.update(
        {"worker_id": worker_id, "market": market}
    )
    FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack = lambda request: {
        "ok": True,
        "quality": {"status": "failed"},
        "profile": {"ticker": request.ticker},
    }
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
        assert captured == {"worker_id": "fundamental_analyst", "market": "CN_A"}
    finally:
        FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools = original_policy
        FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack = original_build


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

    original_policy = FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools
    original_build = FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack
    FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools = lambda worker_id, market: None
    FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack = lambda request: {"ok": True, "ticker": request.ticker}
    try:
        result = FUNDAMENTAL_PACK_MODULE.tool_entrypoint(_Payload(), _Runtime())
        assert result["ticker"] == "600519"

        try:
            FUNDAMENTAL_PACK_MODULE.tool_entrypoint({}, _Runtime())
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", None) == FUNDAMENTAL_PACK_MODULE.FND_TOOL_ENTRYPOINT_INVALID_INPUT
        else:
            raise AssertionError("missing payload.ticker must fail")
    finally:
        FUNDAMENTAL_PACK_MODULE.resolve_fundamental_visible_tools = original_policy
        FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack = original_build


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
