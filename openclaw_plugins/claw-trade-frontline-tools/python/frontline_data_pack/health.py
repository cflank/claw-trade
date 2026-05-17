from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .config import load_frontline_provider_config
from .errors import FrontlineConfigError, FrontlineValidationError, PROVIDER_NOT_ENABLED
from .evidence import OpenVikingEvidenceClient, write_l2_evidence
from .models import L2WriteRequest, L2WriteTarget, ProviderQuery, ProviderSpec
from .mongo_store import (
    check_mongo_health,
    check_mongo_index_health,
    create_mongo_store,
    initialize_mongo_indexes,
)
from .provider_executor import execute_provider_attempt
from .provider_specs import (
    load_fundamental_provider_specs,
    load_market_provider_specs,
    load_news_provider_specs,
    load_social_provider_specs,
)
from .runtime_context import ToolRuntimeContext


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from claw_trade.config.stage_policy import load_stage_policy  # noqa: E402
from claw_trade.config.tool_names import load_tool_registry, resolve_tools  # noqa: E402


TOOL_REGISTRATION_ERROR = "tool_registration_error"
PROVIDER_CONFIG_INVALID = "PROVIDER_CONFIG_INVALID"
L2_STAT_VERIFICATION_FAILED = "L2_STAT_VERIFICATION_FAILED"
EVIDENCE_DIR_ERROR = "EVIDENCE_DIR_ERROR"

EXPECTED_FRONTLINE_VISIBLE_TOOLS_BY_PROFILE: dict[str, dict[str, tuple[str, ...]]] = {
    "CN_A": {
        "market_analyst": ("market_market_data_pack",),
        "fundamental_analyst": ("fundamental_fundamentals_data_pack",),
        "news_analyst": ("news_news_data_pack",),
        "social_analyst": ("social_social_sentiment_pack",),
    },
    "US": {
        "market_analyst": ("get_stock_data", "get_indicators"),
        "fundamental_analyst": ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"),
        "news_analyst": ("get_news", "get_global_news"),
        "social_analyst": ("get_news",),
    },
    "CRYPTO": {
        "market_analyst": ("crypto_market_data_pack",),
        "fundamental_analyst": ("crypto_fundamental_data_pack",),
        "news_analyst": ("crypto_news_data_pack",),
        "social_analyst": ("crypto_social_sentiment_pack",),
    },
}
EXPECTED_FRONTLINE_VISIBLE_TOOLS: dict[str, tuple[str, ...]] = {
    "market_analyst": ("market_market_data_pack",),
    "fundamental_analyst": ("fundamental_fundamentals_data_pack",),
    "news_analyst": ("news_news_data_pack",),
    "social_analyst": ("social_social_sentiment_pack",),
}
EXPECTED_FRONTLINE_TOOL_REGISTRATION = {
    "market_market_data_pack",
    "get_stock_data",
    "get_indicators",
    "fundamental_fundamentals_data_pack",
    "crypto_market_data_pack",
    "crypto_fundamental_data_pack",
    "crypto_news_data_pack",
    "crypto_social_sentiment_pack",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
    "news_news_data_pack",
    "social_social_sentiment_pack",
}


@dataclass(frozen=True)
class HealthResult:
    name: str
    ok: bool
    code: str | None = None
    message: str | None = None
    blocking: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def frontline_tool_health(*, repo_root: Path | None = None, profile: str = "CN_A") -> HealthResult:
    root = _REPO_ROOT if repo_root is None else repo_root
    manifest_path = root / "openclaw_plugins" / "claw-trade-frontline-tools" / "openclaw.plugin.json"

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="frontline_tool.health",
            ok=False,
            code=TOOL_REGISTRATION_ERROR,
            message=f"工具注册清单读取失败: {exc}",
        )

    contracts = payload.get("contracts") if isinstance(payload, Mapping) else None
    declared_tools = contracts.get("tools") if isinstance(contracts, Mapping) else None
    if not isinstance(declared_tools, list):
        return HealthResult(
            name="frontline_tool.health",
            ok=False,
            code=TOOL_REGISTRATION_ERROR,
            message="openclaw.plugin.json 缺少 contracts.tools",
        )

    declared_set = {str(item) for item in declared_tools}
    missing_tools = sorted(EXPECTED_FRONTLINE_TOOL_REGISTRATION - declared_set)
    if missing_tools:
        return HealthResult(
            name="frontline_tool.health",
            ok=False,
            code=TOOL_REGISTRATION_ERROR,
            message="frontline 资料包工具注册不完整",
            details={"missing_tools": missing_tools},
        )

    registry_result = load_tool_registry()
    if not registry_result.ok or registry_result.registry is None:
        return HealthResult(
            name="frontline_tool.health",
            ok=False,
            code=TOOL_REGISTRATION_ERROR,
            message=registry_result.reason or "tool registry 加载失败",
        )

    mismatches: dict[str, dict[str, Any]] = {}
    expected_visible_tools = EXPECTED_FRONTLINE_VISIBLE_TOOLS_BY_PROFILE.get(profile, EXPECTED_FRONTLINE_VISIBLE_TOOLS)
    for worker_id, expected_tools in expected_visible_tools.items():
        policy_result = load_stage_policy(root / "agents", worker_id, profile)
        if not policy_result.ok or policy_result.policy is None:
            mismatches[worker_id] = {
                "error": policy_result.reason or "stage policy 加载失败",
                "source_path": str(policy_result.source_path),
            }
            continue
        try:
            actual = resolve_tools(policy_result.policy, registry_result.registry)
        except Exception as exc:  # noqa: BLE001
            mismatches[worker_id] = {"error": f"resolve_tools 失败: {exc}"}
            continue
        if tuple(actual) != expected_tools:
            mismatches[worker_id] = {
                "expected_tools": list(expected_tools),
                "actual_tools": list(actual),
            }

    if mismatches:
        return HealthResult(
            name="frontline_tool.health",
            ok=False,
            code=TOOL_REGISTRATION_ERROR,
            message="stage policy 工具可见性合同不满足",
            details={"mismatches": mismatches},
        )

    return HealthResult(
        name="frontline_tool.health",
        ok=True,
        details={"registered_tools": sorted(declared_set)},
    )


def frontline_data_pack_health(
    *,
    env: Mapping[str, str] | None = None,
) -> HealthResult:
    try:
        config = load_frontline_provider_config(env)
        store = create_mongo_store(config.mongodb.uri)
    except FrontlineConfigError as exc:
        return HealthResult(
            name="frontline_data_pack.health",
            ok=False,
            code=exc.code,
            message=exc.message,
        )
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="frontline_data_pack.health",
            ok=False,
            code="MONGO_UNAVAILABLE",
            message=str(exc),
        )

    ping_status = check_mongo_health(store.client)
    if not ping_status.ok:
        return HealthResult(
            name="frontline_data_pack.health",
            ok=False,
            code=ping_status.reason or "MONGO_UNAVAILABLE",
            message="MongoDB 健康检查失败",
            details={
                "latency_ms": ping_status.latency_ms,
                "cache_required": config.mongodb.cache_required,
            },
        )

    index_status = check_mongo_index_health(store.database)
    if not index_status.ok:
        return HealthResult(
            name="frontline_data_pack.health",
            ok=False,
            code="MONGO_INDEX_MISSING",
            message="MongoDB 关键索引缺失",
            details={"missing": index_status.missing},
        )

    return HealthResult(
        name="frontline_data_pack.health",
        ok=True,
        details={"latency_ms": ping_status.latency_ms, "database": store.database_name},
    )


def frontline_l2_health(
    *,
    env: Mapping[str, str] | None = None,
    client: OpenVikingEvidenceClient | None = None,
) -> HealthResult:
    request = L2WriteRequest(
        target=L2WriteTarget(
            run_id="preflight-probe",
            stage="frontline",
            worker_id="market_analyst",
            call_id="health-check",
            relative_path="health/l2_probe.json",
            content_type="application/json",
        ),
        content_bytes=b"{\"ok\":true}",
        metadata={"kind": "health_check", "source": "frontline_l2.health"},
    )
    try:
        receipt = write_l2_evidence(request, client=client, env=env)
    except (FrontlineValidationError, FrontlineConfigError) as exc:
        return HealthResult(
            name="frontline_l2.health",
            ok=False,
            code=exc.code,
            message=exc.message,
            details={"blocks_core_evidence": True},
        )
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="frontline_l2.health",
            ok=False,
            code="L2_WRITE_FAILED",
            message=str(exc),
            details={"blocks_core_evidence": True},
        )

    if not receipt.stat_verified:
        return HealthResult(
            name="frontline_l2.health",
            ok=False,
            code=L2_STAT_VERIFICATION_FAILED,
            message="L2 stat 校验未通过",
            details={"uri": receipt.uri, "blocks_core_evidence": True},
        )

    return HealthResult(
        name="frontline_l2.health",
        ok=True,
        details={"uri": receipt.uri, "sha256": receipt.sha256, "stat_verified": True},
    )


def frontline_provider_health(*, env: Mapping[str, str] | None = None) -> HealthResult:
    try:
        config = load_frontline_provider_config(env)
    except FrontlineConfigError as exc:
        return HealthResult(
            name="frontline_provider.health",
            ok=False,
            code=PROVIDER_CONFIG_INVALID,
            message=f"{exc.code}: {exc.message}",
        )

    specs_by_domain: dict[str, list[ProviderSpec]] = {
        "market": load_market_provider_specs(config),
        "news": load_news_provider_specs(config),
        "social": load_social_provider_specs(config),
        "fundamental": load_fundamental_provider_specs(config),
    }
    issues: list[str] = []
    checked_blocked_specs: list[str] = []

    for domain, specs in specs_by_domain.items():
        mainline = [spec for spec in specs if spec.required_for_complete]
        if not mainline:
            issues.append(f"{domain}: 缺少 required_for_complete 主线 provider")
            continue
        enabled_mainline = [spec for spec in mainline if spec.enabled]
        if not enabled_mainline:
            issues.append(f"{domain}: 主线 provider 全部 disabled")

    disabled_remote_specs = [
        spec
        for specs in specs_by_domain.values()
        for spec in specs
        if (not spec.enabled) and spec.mode == "remote"
    ]
    if not disabled_remote_specs:
        issues.append("缺少 disabled remote provider spec，无法验证 config_blocked 行为")
    else:
        probe_query = _probe_query()
        for spec in disabled_remote_specs[:4]:
            result = execute_provider_attempt(
                spec,
                probe_query,
                _probe_context(domain=spec.domain),
            )
            checked_blocked_specs.append(f"{spec.domain}:{spec.provider}/{spec.endpoint}")
            if result.attempt.status != "config_blocked" or result.attempt.error_code != PROVIDER_NOT_ENABLED:
                issues.append(
                    f"{spec.domain}:{spec.provider}/{spec.endpoint} 未返回 config_blocked/PROVIDER_NOT_ENABLED"
                )

    if issues:
        return HealthResult(
            name="frontline_provider.health",
            ok=False,
            code=PROVIDER_CONFIG_INVALID,
            message="provider 配置或关闭策略不满足要求",
            details={"issues": issues, "checked_blocked_specs": checked_blocked_specs},
        )

    return HealthResult(
        name="frontline_provider.health",
        ok=True,
        details={"checked_blocked_specs": checked_blocked_specs},
    )


def frontline_evidence_dir_health(
    *,
    evidence_root: str,
    run_id: str,
    runs_root: Path | None = None,
) -> HealthResult:
    if not evidence_root or "://" in evidence_root:
        return HealthResult(
            name="frontline_evidence_dir.health",
            ok=False,
            code=EVIDENCE_DIR_ERROR,
            message="evidence_root 必须是本地路径",
        )

    base_runs_root = (runs_root or (_REPO_ROOT / "runs")).resolve()
    run_root = (base_runs_root / run_id).resolve()
    evidence_path = Path(evidence_root).resolve()
    if not evidence_path.is_relative_to(run_root):
        return HealthResult(
            name="frontline_evidence_dir.health",
            ok=False,
            code=EVIDENCE_DIR_ERROR,
            message="evidence_root 不在当前 run 目录内",
            details={"run_root": str(run_root), "evidence_root": str(evidence_path)},
        )

    try:
        evidence_path.mkdir(parents=True, exist_ok=True)
        probe_file = evidence_path / ".frontline_evidence_dir_health.tmp"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="frontline_evidence_dir.health",
            ok=False,
            code=EVIDENCE_DIR_ERROR,
            message=f"evidence_root 不可写: {exc}",
            details={"evidence_root": str(evidence_path)},
        )

    return HealthResult(
        name="frontline_evidence_dir.health",
        ok=True,
        details={"evidence_root": str(evidence_path)},
    )


def run_frontline_preflight(
    *,
    env: Mapping[str, str] | None = None,
    profile: str = "CN_A",
    run_id: str = "preflight-probe",
    evidence_root: str | None = None,
    repo_root: Path | None = None,
    l2_client: OpenVikingEvidenceClient | None = None,
) -> dict[str, Any]:
    checks: list[HealthResult] = []
    checks.append(frontline_tool_health(repo_root=repo_root, profile=profile))
    checks.append(frontline_provider_health(env=env))

    index_check = _preflight_index_check(env=env)
    checks.append(index_check)
    checks.append(frontline_data_pack_health(env=env))
    checks.append(frontline_l2_health(env=env, client=l2_client))

    default_evidence_root = str((_REPO_ROOT / "runs" / run_id / "frontline" / "health" / "call" / "evidence").resolve())
    checks.append(
        frontline_evidence_dir_health(
            evidence_root=evidence_root or default_evidence_root,
            run_id=run_id,
        )
    )

    overall_ok = all(item.ok for item in checks if item.blocking)
    return {
        "ok": overall_ok,
        "checks": [item.to_dict() for item in checks],
    }


def _preflight_index_check(*, env: Mapping[str, str] | None) -> HealthResult:
    try:
        config = load_frontline_provider_config(env)
        store = create_mongo_store(config.mongodb.uri)
        created = initialize_mongo_indexes(store.database)
    except FrontlineConfigError as exc:
        return HealthResult(
            name="frontline_preflight.indexes",
            ok=False,
            code=exc.code,
            message=exc.message,
        )
    except Exception as exc:  # noqa: BLE001
        return HealthResult(
            name="frontline_preflight.indexes",
            ok=False,
            code="MONGO_INDEX_CREATE_FAILED",
            message=str(exc),
        )

    created_counts = {name: len(indexes) for name, indexes in created.items()}
    return HealthResult(
        name="frontline_preflight.indexes",
        ok=True,
        details={"collections": created_counts},
    )


def _probe_query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust="qfq",
        query_fingerprint="sha256:" + ("f" * 64),
    )


def _probe_context(*, domain: str) -> ToolRuntimeContext:
    tool_name_by_domain = {
        "market": "market_market_data_pack",
        "news": "news_news_data_pack",
        "social": "social_social_sentiment_pack",
        "fundamental": "fundamental_fundamentals_data_pack",
    }
    worker_by_domain = {
        "market": "market_analyst",
        "news": "news_analyst",
        "social": "social_analyst",
        "fundamental": "fundamental_analyst",
    }
    return ToolRuntimeContext(
        run_id="run-health",
        stage="frontline",
        worker_id=worker_by_domain[domain],
        call_id="call-health",
        dispatch_id="dispatch-health",
        tool_name=tool_name_by_domain[domain],
        evidence_root="runs/run-health/frontline/health/call-health/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )
