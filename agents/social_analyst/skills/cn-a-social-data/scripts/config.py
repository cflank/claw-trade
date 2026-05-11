from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from observability import emit_json_log, record_metric, trace_span

SOCIAL_CONFIG_INVALID = "SOCIAL_CONFIG_INVALID"
SOCIAL_CONFIG_SECRET_MISSING = "SOCIAL_CONFIG_SECRET_MISSING"

SOCIAL_SCHEMA_VERSION = "cn_a_social_pack.v1"
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 10
DEFAULT_PACK_TIMEOUT_SECONDS = 20
DEFAULT_PROVIDER_MAX_CONCURRENCY = 3
DEFAULT_MAX_SIGNALS_PER_BUCKET = 50
DEFAULT_MONGODB_DATABASE = "claw_trade"
DEFAULT_MONGODB_CACHE_COLLECTION = "social_provider_cache"
DEFAULT_CACHE_REQUIRED = False
DEFAULT_TTL_HEAT_SECONDS = 1800
DEFAULT_TTL_KEYWORD_SECONDS = 3600
DEFAULT_TTL_RELATED_SECONDS = 3600
DEFAULT_P1_HOT_UP_ENABLED = True
DEFAULT_P1_XUEQIU_ENABLED = False
DEFAULT_EVIDENCE_ROOT = "runs"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class SocialDataConfig:
    schema_version: str
    provider_timeout_seconds: int
    pack_timeout_seconds: int
    provider_max_concurrency: int
    max_signals_per_bucket: int
    mongodb_uri: str | None
    mongodb_database: str
    mongodb_cache_collection: str
    cache_required: bool
    ttl_by_endpoint: dict[str, int]
    p1_hot_up_enabled: bool
    p1_xueqiu_enabled: bool
    evidence_root: str
    openviking_l2_write_target_root: str | None


class SocialConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def load_social_data_config(env: Mapping[str, str]) -> SocialDataConfig:
    record_metric("social.config.load.count")
    result: SocialDataConfig | None = None
    captured_error: SocialConfigError | None = None
    with trace_span("social.config.load", endpoint="config"):
        try:
            schema_version = _parse_string(env, "CN_A_SOCIAL_SCHEMA_VERSION", default=SOCIAL_SCHEMA_VERSION)
            provider_timeout_seconds = _parse_int(
                env,
                "CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS",
                default=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
            )
            pack_timeout_seconds = _parse_int(
                env,
                "CN_A_SOCIAL_PACK_TIMEOUT_SECONDS",
                default=DEFAULT_PACK_TIMEOUT_SECONDS,
            )
            provider_max_concurrency = _parse_int(
                env,
                "CN_A_SOCIAL_PROVIDER_MAX_CONCURRENCY",
                default=DEFAULT_PROVIDER_MAX_CONCURRENCY,
            )
            max_signals_per_bucket = _parse_int(
                env,
                "CN_A_SOCIAL_MAX_SIGNALS_PER_BUCKET",
                default=DEFAULT_MAX_SIGNALS_PER_BUCKET,
            )
            mongodb_uri = _parse_optional_secret(env, "CN_A_SOCIAL_MONGODB_URI")
            mongodb_database = _parse_string(
                env,
                "CN_A_SOCIAL_MONGODB_DATABASE",
                default=DEFAULT_MONGODB_DATABASE,
            )
            mongodb_cache_collection = _parse_string(
                env,
                "CN_A_SOCIAL_MONGODB_CACHE_COLLECTION",
                default=DEFAULT_MONGODB_CACHE_COLLECTION,
            )
            cache_required, _ = _parse_bool(
                env,
                "CN_A_SOCIAL_CACHE_REQUIRED",
                default=DEFAULT_CACHE_REQUIRED,
            )
            ttl_heat_seconds = _parse_int(env, "CN_A_SOCIAL_TTL_HEAT_SECONDS", default=DEFAULT_TTL_HEAT_SECONDS)
            ttl_keyword_seconds = _parse_int(
                env,
                "CN_A_SOCIAL_TTL_KEYWORD_SECONDS",
                default=DEFAULT_TTL_KEYWORD_SECONDS,
            )
            ttl_related_seconds = _parse_int(
                env,
                "CN_A_SOCIAL_TTL_RELATED_SECONDS",
                default=DEFAULT_TTL_RELATED_SECONDS,
            )
            p1_hot_up_enabled, _ = _parse_bool(
                env,
                "CN_A_SOCIAL_P1_HOT_UP_ENABLED",
                default=DEFAULT_P1_HOT_UP_ENABLED,
            )
            p1_xueqiu_enabled, _ = _parse_bool(
                env,
                "CN_A_SOCIAL_P1_XUEQIU_ENABLED",
                default=DEFAULT_P1_XUEQIU_ENABLED,
            )
            evidence_root = _parse_string(env, "CN_A_SOCIAL_EVIDENCE_ROOT", default=DEFAULT_EVIDENCE_ROOT)
            openviking_l2_write_target_root = _parse_optional_secret(env, "OPENVIKING_L2_WRITE_TARGET_ROOT")

            _validate_schema_version(schema_version)
            _validate_range("CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS", provider_timeout_seconds, 1, 30)
            _validate_range("CN_A_SOCIAL_PACK_TIMEOUT_SECONDS", pack_timeout_seconds, 5, 60)
            if pack_timeout_seconds <= provider_timeout_seconds:
                raise SocialConfigError(
                    SOCIAL_CONFIG_INVALID,
                    "CN_A_SOCIAL_PACK_TIMEOUT_SECONDS 必须大于 CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS",
                )
            _validate_range("CN_A_SOCIAL_PROVIDER_MAX_CONCURRENCY", provider_max_concurrency, 1, 6)
            _validate_range("CN_A_SOCIAL_MAX_SIGNALS_PER_BUCKET", max_signals_per_bucket, 1, 200)
            _validate_positive("CN_A_SOCIAL_TTL_HEAT_SECONDS", ttl_heat_seconds)
            _validate_positive("CN_A_SOCIAL_TTL_KEYWORD_SECONDS", ttl_keyword_seconds)
            _validate_positive("CN_A_SOCIAL_TTL_RELATED_SECONDS", ttl_related_seconds)

            if cache_required and mongodb_uri is None:
                raise SocialConfigError(
                    SOCIAL_CONFIG_SECRET_MISSING,
                    "CN_A_SOCIAL_MONGODB_URI 缺失，无法在 cache_required=true 下运行",
                )

            if p1_xueqiu_enabled:
                raise SocialConfigError(
                    SOCIAL_CONFIG_INVALID,
                    "CN_A_SOCIAL_P1_XUEQIU_ENABLED=true 但缺少批准 endpoint 与字段映射",
                )

            ttl_by_endpoint = {
                "stock_hot_rank_latest_em": ttl_heat_seconds,
                "stock_hot_rank_em": ttl_heat_seconds,
                "stock_hot_up_em": ttl_heat_seconds,
                "stock_hot_keyword_em": ttl_keyword_seconds,
                "stock_hot_rank_relate_em": ttl_related_seconds,
            }

            result = SocialDataConfig(
                schema_version=schema_version,
                provider_timeout_seconds=provider_timeout_seconds,
                pack_timeout_seconds=pack_timeout_seconds,
                provider_max_concurrency=provider_max_concurrency,
                max_signals_per_bucket=max_signals_per_bucket,
                mongodb_uri=mongodb_uri,
                mongodb_database=mongodb_database,
                mongodb_cache_collection=mongodb_cache_collection,
                cache_required=cache_required,
                ttl_by_endpoint=ttl_by_endpoint,
                p1_hot_up_enabled=p1_hot_up_enabled,
                p1_xueqiu_enabled=p1_xueqiu_enabled,
                evidence_root=evidence_root,
                openviking_l2_write_target_root=openviking_l2_write_target_root,
            )
        except SocialConfigError as exc:
            captured_error = exc

    if captured_error is not None:
        raise captured_error
    if result is None:
        raise SocialConfigError(SOCIAL_CONFIG_INVALID, "social 配置加载失败：未生成配置结果")

    emit_json_log(
        level="INFO",
        event="social.config.load",
        code="OK",
        endpoint="config",
        fields={"schema_version": result.schema_version},
    )
    return result


def _parse_int(env: Mapping[str, str], key: str, *, default: int) -> int:
    value = _read_value(env, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SocialConfigError(SOCIAL_CONFIG_INVALID, f"{key} 需要整数") from exc


def _parse_string(env: Mapping[str, str], key: str, *, default: str) -> str:
    value = _read_value(env, key)
    if value is None:
        return default
    if value == "":
        raise SocialConfigError(SOCIAL_CONFIG_INVALID, f"{key} 不能为空")
    return value


def _parse_optional_secret(env: Mapping[str, str], key: str) -> str | None:
    value = _read_value(env, key)
    if value is None or value == "":
        return None
    return value


def _parse_bool(env: Mapping[str, str], key: str, *, default: bool) -> tuple[bool, bool]:
    value = _read_value(env, key)
    if value is None:
        return default, False
    normalized = value.lower()
    if normalized in _TRUE_VALUES:
        return True, True
    if normalized in _FALSE_VALUES:
        return False, True
    raise SocialConfigError(
        SOCIAL_CONFIG_INVALID,
        f"{key} 需要布尔值（true/false）",
    )


def _read_value(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    return value.strip()


def _validate_schema_version(schema_version: str) -> None:
    if schema_version != SOCIAL_SCHEMA_VERSION:
        raise SocialConfigError(
            SOCIAL_CONFIG_INVALID,
            f"schema_version 仅支持 {SOCIAL_SCHEMA_VERSION}",
        )


def _validate_range(key: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise SocialConfigError(
            SOCIAL_CONFIG_INVALID,
            f"{key} 必须在 [{minimum}, {maximum}] 范围内",
        )


def _validate_positive(key: str, value: int) -> None:
    if value <= 0:
        raise SocialConfigError(
            SOCIAL_CONFIG_INVALID,
            f"{key} 需要正整数",
        )
