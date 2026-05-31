from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from claw_trade.selection.data_job import SelectionProviderBatchResult
from claw_trade.selection.models import (
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTION_SRC_ROOT = REPO_ROOT / "src" / "claw_trade" / "selection"
SELECTION_PROVIDER_BATCH = SELECTION_SRC_ROOT / "provider_batch.py"

_FORBIDDEN_PROVIDER_IMPORT_PREFIX = "claw_trade.data_gateway.providers"

_FORBIDDEN_EXECUTION_SYMBOLS = {
    "ProviderRegistry",
    "build_default_provider_adapters",
    "load_default_system_capabilities",
    "build_cn_a_selection_batch_adapters",
    "cn_a_selection_batch_capabilities",
    "ProviderExecutionEvidenceHelper",
}
_FORBIDDEN_CALL_PATTERNS = (
    re.compile(r"^build_.*adapters?$"),
    re.compile(r"^_call_.*(tushare|eastmoney|akshare|baostock).*$"),
)


def _selection_production_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(SELECTION_SRC_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _iter_import_modules(module_ast: ast.Module) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(module_ast):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _call_target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _provider_boundary_probe_plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel13-boundary-contract-probe",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=120,
        universe_scope="all_a_shares",
        provider_batch_plan_ref="plan://selection/cn_a/2026-05-26/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def test_selection_production_code_does_not_import_data_gateway_provider_modules() -> None:
    for path in _selection_production_files():
        imported_modules = _iter_import_modules(_parse_module(path))
        for module in imported_modules:
            assert not module.startswith(
                _FORBIDDEN_PROVIDER_IMPORT_PREFIX
            ), f"{path} imports forbidden provider module path: {module}"


def test_selection_production_code_does_not_build_registry_adapter_or_execute_provider_fetch() -> None:
    for path in _selection_production_files():
        module_ast = _parse_module(path)
        for node in ast.walk(module_ast):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_EXECUTION_SYMBOLS:
                raise AssertionError(f"{path} references forbidden provider execution symbol: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_EXECUTION_SYMBOLS:
                raise AssertionError(f"{path} references forbidden provider execution attribute: {node.attr}")
            if isinstance(node, ast.Call):
                call_target = _call_target_name(node.func)
                if call_target is None:
                    continue
                for pattern in _FORBIDDEN_CALL_PATTERNS:
                    if pattern.match(call_target):
                        raise AssertionError(
                            f"{path} calls forbidden provider execution helper: {call_target} (pattern={pattern.pattern})"
                        )


def test_selection_provider_batch_calls_data_gateway_selection_batch_facade() -> None:
    module_ast = _parse_module(SELECTION_PROVIDER_BATCH)
    data_gateway_imports = [
        module
        for module in _iter_import_modules(module_ast)
        if module.startswith("claw_trade.data_gateway")
    ]
    assert data_gateway_imports, "selection/provider_batch.py must import data_gateway selection façade"
    assert set(data_gateway_imports) == {"claw_trade.data_gateway.selection_batch"}
    assert any(
        isinstance(node, ast.Name) and node.id == "_fetch_selection_batch_from_data_gateway"
        for node in ast.walk(module_ast)
    ), "selection/provider_batch.py must call imported façade alias"


def test_selection_provider_batch_delegates_to_data_gateway_facade_without_touching_provider_execution_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.selection import provider_batch as selection_provider_batch

    plan = _provider_boundary_probe_plan()
    expected = SelectionProviderBatchResult(
        provider_batch_plan=selection_provider_batch.build_selection_provider_batch_plan(
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date=plan.trade_date,
            plan_id=plan.provider_batch_plan_ref,
        ),
        attempt_refs=("attempt://mongo/openbb_provider_attempts/sel13-boundary-probe",),
        normalized_refs=("normalized://mongo/openbb_normalized/sel13-boundary-probe",),
        rows=(),
        data_gaps=(),
    )
    seen: dict[str, object] = {}

    def _poison(*_args, **_kwargs):
        raise AssertionError("selection layer touched data_gateway provider execution detail directly")

    monkeypatch.setattr("claw_trade.data_gateway.selection_batch.ProviderRegistry", _poison)
    monkeypatch.setattr("claw_trade.data_gateway.selection_batch.build_cn_a_selection_batch_adapters", _poison)
    monkeypatch.setattr("claw_trade.data_gateway.selection_batch.cn_a_selection_batch_capabilities", _poison)

    def _fake_facade(run_plan: SelectionRunPlan, *, evidence_root: Path | None = None) -> SelectionProviderBatchResult:
        seen["selection_run_id"] = run_plan.selection_run_id
        seen["market"] = run_plan.market
        seen["profile"] = run_plan.profile
        seen["evidence_root"] = evidence_root
        return expected

    monkeypatch.setattr(selection_provider_batch, "_fetch_selection_batch_from_data_gateway", _fake_facade)

    actual = selection_provider_batch.fetch_selection_batch_from_data_gateway(plan)

    assert seen["selection_run_id"] == plan.selection_run_id
    assert seen["market"] == SelectionMarket.CN_A
    assert seen["profile"] == SelectionProfile.CN_A
    assert seen["evidence_root"] is None
    assert actual is expected
