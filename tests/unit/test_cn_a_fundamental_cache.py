from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_ensure_fundamental_cache_collection_uses_expected_schema_and_collmod() -> None:
    db = _FakeDatabase()
    collection = CACHE_SCHEMA_MODULE.ensure_fundamental_cache_collection(db)
    assert collection.name == CACHE_SCHEMA_MODULE.FUNDAMENTAL_CACHE_COLLECTION_NAME
    assert db.created_collection_name == CACHE_SCHEMA_MODULE.FUNDAMENTAL_CACHE_COLLECTION_NAME
    schema = db.created_validator["$jsonSchema"]
    assert schema["properties"]["ticker"]["pattern"] == r"^[0-9]{6}\.(SH|SZ)$"
    assert schema["properties"]["provider"]["enum"] == ["tushare", "akshare"]
    assert schema["properties"]["schema_version"]["enum"] == ["cn_a_fundamental_pack.v1"]

    db_existing = _FakeDatabase(existing_names={CACHE_SCHEMA_MODULE.FUNDAMENTAL_CACHE_COLLECTION_NAME})
    CACHE_SCHEMA_MODULE.ensure_fundamental_cache_collection(db_existing)
    assert db_existing.coll_mod_command is not None
    assert db_existing.coll_mod_command["collMod"] == CACHE_SCHEMA_MODULE.FUNDAMENTAL_CACHE_COLLECTION_NAME


def test_ensure_fundamental_cache_indexes_creates_required_indexes() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    created = CACHE_SCHEMA_MODULE.ensure_fundamental_cache_indexes(collection)
    assert created == (
        CACHE_SCHEMA_MODULE.INDEX_TICKER_API_PERIOD,
        CACHE_SCHEMA_MODULE.INDEX_PROVIDER_API_PAYLOAD_UNIQUE,
        CACHE_SCHEMA_MODULE.INDEX_PAYLOAD_HASH,
        CACHE_SCHEMA_MODULE.INDEX_TTL_EXPIRE_AT,
    )
    ttl = _find_index(collection.indexes, CACHE_SCHEMA_MODULE.INDEX_TTL_EXPIRE_AT)
    assert ttl["options"]["expireAfterSeconds"] == 0
    unique = _find_index(collection.indexes, CACHE_SCHEMA_MODULE.INDEX_PROVIDER_API_PAYLOAD_UNIQUE)
    assert unique["options"]["unique"] is True


def test_create_mongo_client_accepts_local_uri_and_enforces_authsource_when_credentials_present() -> None:
    config = CONFIG_MODULE.FundamentalDataConfig(
        tushare_token=None,
        mongodb_uri="mongodb://localhost:27017",
        mongodb_database="claw_trade",
        mongodb_cache_collection="cn_a_fundamental_cache",
        openviking_endpoint="https://openviking.example.internal",
        openviking_api_key="secret",
        openviking_workspace="workspace-a",
        tool_enabled=True,
        disable_tushare=False,
        disable_akshare=False,
        cache_read_only=False,
    )
    client = CACHE_MODULE.create_mongo_client(config)
    try:
        assert client.options.pool_options.max_pool_size == 10
        assert client.options.pool_options.min_pool_size == 1
        assert client.options.server_selection_timeout == 3.0
        assert client.options.pool_options.connect_timeout == 3.0
    finally:
        client.close()

    bad_auth = _replace_dataclass(config, mongodb_uri="mongodb://user:pass@localhost:27017")
    try:
        CACHE_MODULE.create_mongo_client(bad_auth)
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", None) == CONFIG_MODULE.FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED
    else:
        raise AssertionError("authSource 缺失时必须报错")


def test_inspect_cache_returns_reusable_fields_and_diagnostics_without_ok_shortcut() -> None:
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    records = [
        _cache_record(
            provider="tushare",
            api_name="daily_basic",
            fields={
                "valuation.pe_ttm": {
                    "field_path": "valuation.pe_ttm",
                    "value": 12.3,
                    "unit": "ratio",
                    "scale": "x",
                }
            },
            expires_at=now + timedelta(days=1),
        ),
        _cache_record(
            provider="tushare",
            api_name="daily_basic",
            fields={
                "valuation.pb": {
                    "field_path": "valuation.pb",
                    "value": 2.1,
                    "unit": "ratio",
                    "scale": "x",
                }
            },
            expires_at=now - timedelta(seconds=1),
        ),
        _cache_record(
            provider="tushare",
            api_name="fina_indicator",
            fields={
                "financial_indicators.roe": {
                    "field_path": "financial_indicators.roe",
                    "value": 19.2,
                    "unit": None,
                    "scale": None,
                }
            },
            expires_at=None,
        ),
    ]
    collection = _FakeCollection("cn_a_fundamental_cache", records)
    normalized = _normalized_input()
    result = CACHE_MODULE.inspect_fundamental_cache(collection, normalized, now=now)
    assert "valuation.pe_ttm" in result.reusable_fields
    cache_field = result.reusable_fields["valuation.pe_ttm"]
    assert cache_field["provider"] == "tushare"
    assert cache_field["api_name"] == "daily_basic"
    assert cache_field["payload_hash"] == "sha256:" + "a" * 64
    assert (
        cache_field["raw_payload_ref"]
        == "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/cache/fundamental/1.json"
    )
    assert cache_field["report_period"] == "20251231"
    assert cache_field["announce_date"] == "2026-03-31"
    assert cache_field["as_of"] == "2026-05-07"
    assert cache_field["fetched_at"] == "2026-05-07T00:00:00+00:00"
    assert cache_field["source_ref_path"] == "field_sources.valuation.pe_ttm"
    assert "valuation.pb" not in result.reusable_fields
    assert result.attempt.status == "success"
    assert not hasattr(result, "ok")
    assert any("cache.stale" in item for item in result.diagnostics)
    assert any("cache.untrusted" in item for item in result.diagnostics)


def test_inspect_cache_does_not_override_conflict_between_providers() -> None:
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    records = [
        _cache_record(
            provider="tushare",
            api_name="daily_basic",
            fields={
                "valuation.pe_ttm": {
                    "field_path": "valuation.pe_ttm",
                    "value": 12.3,
                    "unit": "ratio",
                    "scale": "x",
                }
            },
            expires_at=now + timedelta(days=1),
        ),
        _cache_record(
            provider="akshare",
            api_name="stock_zh_a_spot_em",
            fields={
                "valuation.pe_ttm": {
                    "field_path": "valuation.pe_ttm",
                    "value": 31.0,
                    "unit": "ratio",
                    "scale": "x",
                }
            },
            expires_at=now + timedelta(days=1),
        ),
    ]
    collection = _FakeCollection("cn_a_fundamental_cache", records)
    result = CACHE_MODULE.inspect_fundamental_cache(collection, _normalized_input(), now=now)
    assert "valuation.pe_ttm" not in result.reusable_fields
    assert any("cross_provider_conflict:valuation.pe_ttm" in item for item in result.diagnostics)


def test_upsert_cache_obeys_read_only_and_only_sets_ttl_for_price_or_valuation() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    result = CACHE_MODULE.upsert_fundamental_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="income",
            fields=[("income_statement.revenue", 100.0, "field_sources.income_statement.revenue")],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=True,
    )
    assert result.status == "skipped"
    assert result.reason == CACHE_MODULE.FND_CACHE_READ_ONLY
    assert len(collection.upserts) == 0

    write_result = CACHE_MODULE.upsert_fundamental_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="daily_basic",
            fields=[("valuation.pe_ttm", 12.3, "field_sources.valuation.pe_ttm")],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert write_result.status == "upserted"
    last = collection.upserts[-1]
    assert last["filter"]["provider"] == "tushare"
    assert last["filter"]["api_name"] == "daily_basic"
    assert last["update"]["$set"]["expires_at"] is not None

    write_result_no_ttl = CACHE_MODULE.upsert_fundamental_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="income",
            fields=[("income_statement.revenue", 88.0, "field_sources.income_statement.revenue")],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert write_result_no_ttl.status == "upserted"
    last_no_ttl = collection.upserts[-1]
    assert last_no_ttl["update"]["$set"]["expires_at"] is None


def test_maybe_write_cache_skips_when_raw_payload_missing() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    result = CACHE_MODULE.maybe_write_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="income",
            fields=[("income_statement.revenue", 100.0, "field_sources.income_statement.revenue")],
            raw_payload_hash=None,
            raw_payload_ref=None,
            attempt_raw_payload_hash=None,
            attempt_raw_payload_ref=None,
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert result.status == "skipped"
    assert result.reason == CACHE_MODULE.FND_CACHE_WRITE_RAW_PAYLOAD_MISSING
    assert len(collection.upserts) == 0


def test_maybe_write_cache_skips_when_no_mapped_fields() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    result = CACHE_MODULE.maybe_write_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="income",
            fields=[],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert result.status == "skipped"
    assert result.reason == CACHE_MODULE.FND_CACHE_WRITE_NO_MAPPED_FIELDS
    assert len(collection.upserts) == 0


def test_maybe_write_cache_skips_when_cache_read_only() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    result = CACHE_MODULE.maybe_write_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="income",
            fields=[("income_statement.revenue", 100.0, "field_sources.income_statement.revenue")],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=True,
    )
    assert result.status == "skipped"
    assert result.reason == CACHE_MODULE.FND_CACHE_WRITE_READ_ONLY
    assert len(collection.upserts) == 0


def test_maybe_write_cache_calls_upsert_only_after_gate_passes() -> None:
    collection = _FakeCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    result = CACHE_MODULE.maybe_write_cache(
        collection,
        normalized,
        _provider_result(
            provider="tushare",
            api_name="daily_basic",
            fields=[("valuation.pe_ttm", 12.3, "field_sources.valuation.pe_ttm")],
        ),
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert result.status == "upserted"
    assert len(collection.upserts) == 1


def test_maybe_write_cache_mongo_failure_returns_cache_diagnostic_without_mutating_provider_status() -> None:
    collection = _FailingCollection("cn_a_fundamental_cache", [])
    normalized = _normalized_input()
    provider_result = _provider_result(
        provider="tushare",
        api_name="daily_basic",
        fields=[("valuation.pe_ttm", 12.3, "field_sources.valuation.pe_ttm")],
    )
    result = CACHE_MODULE.maybe_write_cache(
        collection,
        normalized,
        provider_result,
        metric_definition_version="fnd.metric.v1",
        cache_read_only=False,
    )
    assert result.status == "error"
    assert result.reason is not None
    assert provider_result.attempt.status == "success"


def _normalized_input():
    return MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2026-01-01",
        end_date="2026-05-01",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
    )


def _cache_record(
    *,
    provider: str,
    api_name: str,
    fields: dict[str, dict[str, Any]],
    expires_at: datetime | None,
) -> dict[str, Any]:
    now = datetime(2026, 5, 7, 0, 0, 0, tzinfo=UTC)
    return {
        "ticker": "600519.SH",
        "market": "CN_A",
        "provider": provider,
        "api_name": api_name,
        "report_period": "20251231",
        "announce_date": "2026-03-31",
        "as_of": "2026-05-07",
        "fetched_at": now,
        "schema_version": "cn_a_fundamental_pack.v1",
        "payload_hash": "sha256:" + "a" * 64,
        "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/cache/fundamental/1.json",
        "metric_definition_version": "fnd.metric.v1",
        "expires_at": expires_at,
        "fields": fields,
        "created_at": now,
        "updated_at": now,
    }


def _provider_result(
    *,
    provider: str,
    api_name: str,
    fields: list[tuple[str, Any, str]],
    raw_payload_hash: str | None = "sha256:" + "b" * 64,
    raw_payload_ref: str | None = (
        "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/cache/fundamental/2.json"
    ),
    attempt_raw_payload_hash: str | None = "sha256:" + "b" * 64,
    attempt_raw_payload_ref: str | None = (
        "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/cache/fundamental/2.json"
    ),
):
    attempt = MODELS_MODULE.ProviderAttempt(
        provider=provider,
        role="primary",
        api_name=api_name,
        attempt_seq=1,
        status="success",
        reason=None,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={},
        response_row_count=1,
        response_col_count=1,
        field_coverage=[item[0] for item in fields],
        report_period="20251231",
        announce_date="2026-03-31",
        as_of="2026-05-07",
        fetched_at="2026-05-07T00:00:00+00:00",
        raw_payload_hash=attempt_raw_payload_hash,
        raw_payload_ref=attempt_raw_payload_ref,
        error_type=None,
        error_message_redacted=None,
    )
    return MODELS_MODULE.ProviderResult(
        attempt=attempt,
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        extracted_fields=fields,
        schema_changed=False,
    )


def _replace_dataclass(config, **changes):
    payload = dict(config.__dict__)
    payload.update(changes)
    return CONFIG_MODULE.FundamentalDataConfig(**payload)


def _find_index(indexes: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for index in indexes:
        if index["name"] == name:
            return index
    raise AssertionError(f"index not found: {name}")


class _FakeDatabase:
    def __init__(self, existing_names: set[str] | None = None) -> None:
        self._names = set(existing_names or set())
        self._collections: dict[str, _FakeCollection] = {}
        self.created_collection_name: str | None = None
        self.created_validator: dict[str, Any] | None = None
        self.coll_mod_command: dict[str, Any] | None = None

    def list_collection_names(self, filter: dict[str, str] | None = None) -> list[str]:
        if filter is None:
            return sorted(self._names)
        target = filter.get("name")
        if target is None:
            return sorted(self._names)
        return [name for name in self._names if name == target]

    def create_collection(self, name: str, **kwargs: Any) -> None:
        self._names.add(name)
        self.created_collection_name = name
        self.created_validator = kwargs.get("validator")
        self._collections[name] = _FakeCollection(name, [])

    def command(self, command: dict[str, Any]) -> None:
        self.coll_mod_command = command

    def get_collection(self, name: str):
        existing = self._collections.get(name)
        if existing is not None:
            return existing
        created = _FakeCollection(name, [])
        self._collections[name] = created
        return created


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = list(documents)

    def sort(self, sort_spec: list[tuple[str, int]]):
        docs = list(self._documents)
        for key, order in reversed(sort_spec):
            reverse = order < 0
            docs.sort(key=lambda item: item.get(key), reverse=reverse)
        return _FakeCursor(docs)

    def __iter__(self):
        return iter(self._documents)


class _FakeUpdateResult:
    def __init__(self, matched_count: int, modified_count: int, upserted_id: Any) -> None:
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeCollection:
    def __init__(self, name: str, records: list[dict[str, Any]]) -> None:
        self.name = name
        self._records = [dict(record) for record in records]
        self.indexes: list[dict[str, Any]] = []
        self.upserts: list[dict[str, Any]] = []

    def create_index(self, keys: list[tuple[str, int]], **options: Any) -> str:
        name = options.get("name")
        self.indexes.append({"name": name, "keys": list(keys), "options": dict(options)})
        return str(name)

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        result: list[dict[str, Any]] = []
        for record in self._records:
            if record.get("ticker") != query.get("ticker"):
                continue
            if record.get("market") != query.get("market"):
                continue
            api_name_clause = query.get("api_name")
            if isinstance(api_name_clause, Mapping):
                approved = api_name_clause.get("$in")
                if isinstance(approved, list) and record.get("api_name") not in approved:
                    continue
            result.append(dict(record))
        return _FakeCursor(result)

    def update_one(self, filter_doc: dict[str, Any], update_doc: dict[str, Any], upsert: bool = False):
        self.upserts.append({"filter": dict(filter_doc), "update": update_doc, "upsert": upsert})
        for record in self._records:
            if _matches_filter(record, filter_doc):
                record.update(dict(update_doc.get("$set", {})))
                return _FakeUpdateResult(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            created = dict(filter_doc)
            created.update(dict(update_doc.get("$set", {})))
            created.update(dict(update_doc.get("$setOnInsert", {})))
            self._records.append(created)
            return _FakeUpdateResult(matched_count=0, modified_count=0, upserted_id=f"upserted-{len(self._records)}")
        return _FakeUpdateResult(matched_count=0, modified_count=0, upserted_id=None)


class _FailingCollection(_FakeCollection):
    def update_one(self, filter_doc: dict[str, Any], update_doc: dict[str, Any], upsert: bool = False):
        raise RuntimeError("mongo write failed")


def _matches_filter(record: Mapping[str, Any], filter_doc: Mapping[str, Any]) -> bool:
    for key, value in filter_doc.items():
        if record.get(key) != value:
            return False
    return True


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}"] = module
    spec.loader.exec_module(module)
    return module


CONFIG_MODULE = _load_script_module("config")
MODELS_MODULE = _load_script_module("models")
CACHE_SCHEMA_MODULE = _load_script_module("cache_schema")
CACHE_MODULE = _load_script_module("cache")
