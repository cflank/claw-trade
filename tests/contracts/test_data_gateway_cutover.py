from __future__ import annotations

import builtins
import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_GATEWAY_ROOT = REPO_ROOT / "src" / "claw_trade" / "data_gateway"

_DG = "data_" + "gateway"
_DGB = _DG + "_bak"
_LEGACY_RUNTIME = "open" + "bb_runtime"
_LEGACY_STORE_PREFIX = "open" + "bb_"
_LEGACY_MODE = "open" + "bb"
_RUN_PROVIDER_PLAN = "run_" + "provider_" + "plan"

FORBIDDEN_LEGACY_PREFIXES = (
    f"claw_trade.{_DGB}",
    f"claw_trade.{_DGB}.packs",
    f"claw_trade.{_DGB}.mcp",
    f"claw_trade.{_DGB}.openviking",
    f"claw_trade.{_DGB}.providers.{_LEGACY_RUNTIME}",
    f"claw_trade.{_DGB}.store",
    f"claw_trade.{_DGB}.store.{_LEGACY_STORE_PREFIX}",
    f"claw_trade.{_DG}.packs",
    f"claw_trade.{_DG}.mcp",
    f"claw_trade.{_DG}.openviking",
    f"claw_trade.{_DG}.mcp.runtime_wrapper",
    f"claw_trade.{_DG}.providers.{_LEGACY_RUNTIME}",
    f"claw_trade.{_DG}.store.{_LEGACY_STORE_PREFIX}",
)


class _TrapModule(ModuleType):
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"legacy path touched: {self.__name__}.{name}")


class _DependencyNotImplemented:
    def __getattr__(self, name: str) -> Any:
        def _raise(*args: object, **kwargs: object) -> object:
            raise NotImplementedError(f"dependency not implemented: {name}")

        return _raise


def _is_forbidden_module(name: str) -> bool:
    for prefix in FORBIDDEN_LEGACY_PREFIXES:
        if prefix.endswith("_"):
            if name.startswith(prefix):
                return True
            continue
        if name == prefix or name.startswith(f"{prefix}."):
            return True
    return False


def _install_legacy_import_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> Any:
        if _is_forbidden_module(name):
            raise AssertionError(f"forbidden legacy import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    for module_name in FORBIDDEN_LEGACY_PREFIXES:
        if module_name.endswith("_"):
            continue
        monkeypatch.setitem(sys.modules, module_name, _TrapModule(module_name))


def _load_data_api_symbols() -> type[Any]:
    module = importlib.import_module("claw_trade.data_gateway.api")
    data_api_cls = getattr(module, "DataAPI", None)
    if data_api_cls is None:
        pytest.fail("DLT-13 blocked: claw_trade.data_gateway.api exists but DataAPI class is missing")
    if not callable(getattr(data_api_cls, "request_data", None)):
        pytest.fail("DLT-13 blocked: DataAPI must provide request_data")
    if hasattr(data_api_cls, "get_data_needs"):
        pytest.fail("DLT-13 violation: DataAPI must not expose legacy get_data_needs")
    if hasattr(data_api_cls, "get_data") or hasattr(data_api_cls, "get_data_batch"):
        pytest.fail("DLT-13 violation: DataAPI must not expose get_data/get_data_batch")
    return data_api_cls


def _instantiate_data_api(data_api_cls: type[Any]) -> Any:
    signature = inspect.signature(data_api_cls)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if parameter.default is not inspect.Parameter.empty:
            continue
        kwargs[name] = _DependencyNotImplemented()
    return data_api_cls(**kwargs)


def _assert_no_legacy_success(result: Any) -> None:
    status = None
    if isinstance(result, dict):
        status = result.get("status")
    else:
        status = getattr(result, "status", None)
    if status is None:
        return
    assert status not in {"ready", "success", "completed"}, (
        "DLT-13 violation: request path missing implementation must return gap/error, not legacy success"
    )


def test_cutover_requires_new_data_api_surface() -> None:
    try:
        _load_data_api_symbols()
    except ModuleNotFoundError as exc:
        pytest.fail(f"DLT-13 blocked: missing new module claw_trade.data_gateway.api ({exc})")


def test_data_api_does_not_expose_legacy_data_request_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_legacy_import_blocker(monkeypatch)
    data_api_cls = _load_data_api_symbols()
    api = _instantiate_data_api(data_api_cls)

    assert not hasattr(api, "get_data")
    assert not hasattr(api, "get_data_batch")


def test_new_data_gateway_source_does_not_reference_legacy_paths() -> None:
    assert DATA_GATEWAY_ROOT.exists(), "missing src/claw_trade/data_gateway"
    py_files = sorted(DATA_GATEWAY_ROOT.rglob("*.py"))
    assert py_files, "DLT-13 blocked: new data_gateway package has no python source files"

    forbidden_tokens = (
        _DGB,
        f"{_DGB}/packs",
        f"{_DGB}/mcp/runtime_wrapper.py",
        f"{_DGB}/openviking/",
        f"{_DGB}/providers/{_LEGACY_RUNTIME}.py",
        "mcp/runtime_wrapper.py",
        "openviking/",
        f"providers/{_LEGACY_RUNTIME}.py",
        f"claw_trade.{_DG}.packs",
        f"claw_trade.{_DGB}.packs",
        f"claw_trade.{_DGB}.mcp.runtime_wrapper",
        f"claw_trade.{_DGB}.openviking",
        f"claw_trade.{_DGB}.providers.{_LEGACY_RUNTIME}",
        f"claw_trade.{_DG}.mcp.runtime_wrapper",
        f"claw_trade.{_DG}.openviking",
        f"claw_trade.{_DG}.providers.{_LEGACY_RUNTIME}",
        f"claw_trade.{_DG}.store.{_LEGACY_STORE_PREFIX}",
        f"claw_trade.{_DGB}.store.{_LEGACY_STORE_PREFIX}",
    )
    hits: list[tuple[str, str]] = []
    for file in py_files:
        text = file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                hits.append((str(file.relative_to(REPO_ROOT)), token))
    assert not hits, f"DLT-13 violation: new data_gateway still references legacy path tokens: {hits}"


def test_run_control_and_runner_cutover_do_not_bind_legacy_runtime_modules() -> None:
    run_control = (REPO_ROOT / "src" / "claw_trade" / "cli" / "run_control.py").read_text(encoding="utf-8")
    runner = (REPO_ROOT / "src" / "claw_trade" / "workflow" / "runner.py").read_text(encoding="utf-8")
    forbidden_tokens = (
        f"claw_trade.{_DG}.store",
        f"claw_trade.{_DG}.providers.defaults",
        f"claw_trade.{_DG}.providers.run_plan",
        "LegacyMongoLineageWriter",
        f'{_DG}_mode="{_LEGACY_MODE}"',
        f"{_DG}={_LEGACY_MODE}",
        f"report_command 仅允许 {_DG}={_LEGACY_MODE}",
        _RUN_PROVIDER_PLAN + "ner",
        _RUN_PROVIDER_PLAN + "_store",
        "run_" + "provider_registry",
        "provider_config_version_resolver",
        _RUN_PROVIDER_PLAN,
    )
    hits: list[tuple[str, str]] = []
    for token in forbidden_tokens:
        if token in run_control:
            hits.append(("src/claw_trade/cli/run_control.py", token))
        if token in runner:
            hits.append(("src/claw_trade/workflow/runner.py", token))
    assert not hits, f"DLT-13 violation: legacy runtime bindings still present: {hits}"
