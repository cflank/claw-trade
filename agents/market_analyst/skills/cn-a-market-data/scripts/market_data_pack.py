from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


MARKET_DATA_PACK_EXECUTION_FAILED = "MARKET_DATA_PACK_EXECUTION_FAILED"


def run_market_data_pack(
    tool_input: Mapping[str, Any] | Any,
    runtime_context: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    try:
        runtime_run_market_data_pack, to_jsonable = _load_frontline_pack_runtime()
        pack = runtime_run_market_data_pack(tool_input, runtime_context)
        jsonable = to_jsonable(pack)
        if isinstance(jsonable, Mapping):
            return {str(key): jsonable[key] for key in jsonable.keys()}
        raise ValueError("run_market_data_pack 返回结果不可序列化为 dict")
    except Exception as exc:  # noqa: BLE001
        return _build_structured_tool_error(exc)


def _load_frontline_pack_runtime():
    shared_python_root = Path(__file__).resolve().parents[5] / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
    if str(shared_python_root) not in sys.path:
        sys.path.insert(0, str(shared_python_root))

    from frontline_data_pack.market_data_pack import run_market_data_pack as runtime_run_market_data_pack
    from frontline_data_pack.models import to_jsonable

    return runtime_run_market_data_pack, to_jsonable


def _build_structured_tool_error(exc: Exception) -> dict[str, Any]:
    error_code = getattr(exc, "code", MARKET_DATA_PACK_EXECUTION_FAILED)
    error_message = getattr(exc, "message", str(exc))
    return {
        "ok": False,
        "error": {
            "code": str(error_code),
            "message": str(error_message),
        },
        "quality": {
            "status": "failed",
            "warnings": [
                {
                    "code": str(error_code),
                    "message": str(error_message),
                }
            ],
        },
        "reader_brief": f"行情资料包执行失败：{error_code}。{error_message}",
    }
