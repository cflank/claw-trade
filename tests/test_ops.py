from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_OPS_PATH = SOCIAL_SCRIPTS_ROOT / "ops.py"


@dataclass(frozen=True)
class _Config:
    schema_version: str = "cn_a_social_pack.v1"
    provider_timeout_seconds: int = 1
    pack_timeout_seconds: int = 2
    provider_max_concurrency: int = 3
    max_signals_per_bucket: int = 50
    mongodb_uri: str | None = None
    mongodb_database: str = "claw_trade"
    mongodb_cache_collection: str = "social_provider_cache"
    cache_required: bool = False
    ttl_by_endpoint: dict[str, int] = None  # type: ignore[assignment]
    p1_hot_up_enabled: bool = True
    p1_xueqiu_enabled: bool = False
    evidence_root: str = "runs"
    openviking_l2_write_target_root: str | None = None

    def __post_init__(self) -> None:
        if self.ttl_by_endpoint is None:
            object.__setattr__(self, "ttl_by_endpoint", {})


@dataclass
class _FakeClock:
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_startup_check_fails_when_cache_required_and_mongodb_unreachable() -> None:
    config = _Config(cache_required=True, mongodb_uri="mongodb://example.invalid:27017")

    result = OPS_MODULE.run_startup_checks(
        config,
        writer=object(),
        mongodb_probe=lambda _cfg: (False, "MongoDB connect failed"),
        openviking_binding_probe=lambda _writer: (True, None),
        akshare_probe=lambda _cfg: (True, None),
    )

    assert result.ok is False
    mongo_check = next(item for item in result.checks if item.name == "mongodb_connectivity")
    assert mongo_check.ok is False
    assert mongo_check.blocking is True


def test_openviking_auth_failed_maps_to_high_priority_alert() -> None:
    event = OPS_MODULE.build_alert_event(
        code=OPS_MODULE.SOCIAL_OPENVIKING_AUTH_FAILED,
        message="auth denied",
        component="openviking",
    )
    assert event.priority == OPS_MODULE.ALERT_PRIORITY_HIGH


def test_provider_timeout_maps_to_medium_priority_alert() -> None:
    event = OPS_MODULE.build_alert_event(
        code=OPS_MODULE.SOCIAL_PROVIDER_TIMEOUT,
        message="provider timeout",
        component="provider",
    )
    assert event.priority == OPS_MODULE.ALERT_PRIORITY_MEDIUM


def test_shutdown_stops_new_calls_and_marks_unfinished_endpoints_cancelled_after_timeout() -> None:
    clock = _FakeClock(now=0.0)
    controller = OPS_MODULE.GracefulShutdownController(
        pack_timeout_seconds=2,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert controller.begin_pack("pack-1", ("stock_hot_rank_em", "stock_hot_keyword_em")) is True
    controller.mark_endpoint_complete("pack-1", "stock_hot_rank_em")

    controller.begin_shutdown()
    assert controller.accepting_new_calls is False
    assert controller.begin_pack("pack-2", ("stock_hot_up_em",)) is False

    drain_result = controller.drain(poll_interval_seconds=0.5)
    assert drain_result.drained is False
    assert drain_result.cancelled == (
        OPS_MODULE.CancelledEndpoint(pack_id="pack-1", endpoint="stock_hot_keyword_em"),
    )
    assert "pack-1" in drain_result.remaining_pack_ids


def _load_ops_module():
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    module_name = "cn_a_social_ops"
    spec = importlib.util.spec_from_file_location(module_name, SOCIAL_OPS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载运维模块: {SOCIAL_OPS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


OPS_MODULE = _load_ops_module()
