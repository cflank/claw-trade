from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.health import HealthResult, run_frontline_preflight  # noqa: E402


def test_t_ops_001_preflight_checks_include_indexes_health_tools_and_stage_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_provider_health",
        lambda **_kwargs: HealthResult(name="frontline_provider.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health._preflight_index_check",
        lambda **_kwargs: HealthResult(name="frontline_preflight.indexes", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_data_pack_health",
        lambda **_kwargs: HealthResult(name="frontline_data_pack.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_l2_health",
        lambda **_kwargs: HealthResult(name="frontline_l2.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_evidence_dir_health",
        lambda **_kwargs: HealthResult(name="frontline_evidence_dir.health", ok=True),
    )

    result = run_frontline_preflight()

    assert result["ok"] is True
    names = [item["name"] for item in result["checks"]]
    assert "frontline_tool.health" in names
    assert "frontline_preflight.indexes" in names
    assert "frontline_data_pack.health" in names
    assert "frontline_l2.health" in names
    assert "frontline_provider.health" in names
    assert "frontline_evidence_dir.health" in names


def test_t_ops_001_preflight_is_blocked_when_tool_registration_contract_check_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_tool_health",
        lambda **_kwargs: HealthResult(
            name="frontline_tool.health",
            ok=False,
            code="tool_registration_error",
            message="frontline 资料包工具注册不完整",
        ),
    )
    _patch_non_tool_checks_as_healthy(monkeypatch)

    result = run_frontline_preflight()

    assert result["ok"] is False
    tool_check = _check_by_name(result, "frontline_tool.health")
    assert tool_check["ok"] is False
    assert tool_check["code"] == "tool_registration_error"


def test_t_ops_001_preflight_is_blocked_when_stage_policy_contract_check_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_tool_health",
        lambda **_kwargs: HealthResult(
            name="frontline_tool.health",
            ok=False,
            code="tool_registration_error",
            message="stage policy 工具可见性合同不满足",
            details={
                "mismatches": {
                    "news_analyst": {
                        "expected_tools": ["news_news_data_pack"],
                        "actual_tools": ["news_news_data_pack", "openviking_write_material"],
                    }
                }
            },
        ),
    )
    _patch_non_tool_checks_as_healthy(monkeypatch)

    result = run_frontline_preflight()

    assert result["ok"] is False
    tool_check = _check_by_name(result, "frontline_tool.health")
    assert tool_check["ok"] is False
    assert tool_check["code"] == "tool_registration_error"
    assert tool_check["message"] == "stage policy 工具可见性合同不满足"


def _patch_non_tool_checks_as_healthy(monkeypatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_provider_health",
        lambda **_kwargs: HealthResult(name="frontline_provider.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health._preflight_index_check",
        lambda **_kwargs: HealthResult(name="frontline_preflight.indexes", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_data_pack_health",
        lambda **_kwargs: HealthResult(name="frontline_data_pack.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_l2_health",
        lambda **_kwargs: HealthResult(name="frontline_l2.health", ok=True),
    )
    monkeypatch.setattr(
        "frontline_data_pack.health.frontline_evidence_dir_health",
        lambda **_kwargs: HealthResult(name="frontline_evidence_dir.health", ok=True),
    )


def _check_by_name(result: dict[str, object], name: str) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    for check in checks:
        assert isinstance(check, dict)
        if check.get("name") == name:
            return check
    raise AssertionError(f"missing check: {name}")
