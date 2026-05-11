from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_PROFILE_PATH = SOCIAL_SCRIPTS_ROOT / "profile.py"
SOCIAL_ORCHESTRATOR_PATH = SOCIAL_SCRIPTS_ROOT / "orchestrator.py"


def test_normalize_cn_a_ticker_returns_invalid_prefix_code_for_bad_input() -> None:
    with pytest.raises(PROFILE_MODULE.SocialProfileError) as exc_info:
        PROFILE_MODULE.NormalizeCnATicker("12345")

    assert exc_info.value.code == PROFILE_MODULE.SOCIAL_INVALID_TICKER_MARKET_PREFIX


def test_orchestrator_maps_invalid_ticker_to_social_invalid_ticker() -> None:
    pack = ORCH_MODULE.build_social_sentiment_pack(
        tool_input=ORCH_MODULE.SocialToolInput(
            ticker="12345",
            market="CN_A",
        ),
        context=_runtime_context(),
        config=_config(),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert _first_warning_code(pack) == ORCH_MODULE.SOCIAL_INVALID_TICKER


def _first_warning_code(pack: object) -> str | None:
    quality = getattr(pack, "quality", None)
    warnings = getattr(quality, "warnings", None)
    if not isinstance(warnings, list) or not warnings:
        return None
    first = warnings[0]
    if isinstance(first, dict):
        value = first.get("code")
        if isinstance(value, str):
            return value
    return None


def _runtime_context() -> object:
    return ORCH_MODULE.SocialToolRuntimeContext(
        run_id="run-test-1",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-test-1",
        tool_name="social_social_sentiment_pack",
        evidence_root="runs",
        current_time="2026-05-07T10:00:00+08:00",
    )


def _config() -> object:
    return ORCH_MODULE.SocialDataConfig(
        schema_version="cn_a_social_pack.v1",
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=None,
        mongodb_database="claw_trade",
        mongodb_cache_collection="social_provider_cache",
        cache_required=False,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
        },
        p1_hot_up_enabled=False,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _load_module(module_name: str, module_path: Path):
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


PROFILE_MODULE = _load_module("cn_a_social_profile_unit", SOCIAL_PROFILE_PATH)
ORCH_MODULE = _load_module("cn_a_social_orchestrator_unit", SOCIAL_ORCHESTRATOR_PATH)
