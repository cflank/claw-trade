from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config_loader import load_alias_conflict_blacklist, load_cn_a_news_config
from errors import E_PROFILE_RESOLVE_FAILED, NewsDataError
from models import ResolvedProfile

_LOGGER = logging.getLogger(__name__)

_SCALAR_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "company_name": ("company_name", "company", "name"),
    "industry": ("industry", "sector", "sw_industry"),
}

_LIST_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "approved_aliases": ("approved_aliases", "aliases"),
    "approved_historical_names": ("approved_historical_names", "historical_names"),
}

_SOURCE_ORDER: tuple[tuple[str, str | None], ...] = (
    ("runtime_profile", None),
    ("fundamentals", None),
    ("market", None),
)
_ALLOWED_MISSING_FIELDS: tuple[str, ...] = (
    "company_name",
    "industry",
    "approved_aliases",
    "approved_historical_names",
)


class ApprovedProfileResolver:
    def resolve(
        self,
        ticker: str,
        run_id: str,
        stage: str,
        profile_ref: str | None,
        fundamentals_ref: str | None,
        market_ref: str | None,
    ) -> ResolvedProfile:
        source_refs = {
            "runtime_profile": profile_ref,
            "fundamentals": fundamentals_ref,
            "market": market_ref,
        }
        source_payloads = self._load_source_payloads(
            ticker=ticker,
            run_id=run_id,
            stage=stage,
            source_refs=source_refs,
        )
        alias_conflict_blacklist = self._load_alias_conflict_blacklist()

        company_name = self._resolve_scalar(
            field="company_name",
            source_payloads=source_payloads,
            ticker=ticker,
            run_id=run_id,
            stage=stage,
        )
        industry = self._resolve_scalar(
            field="industry",
            source_payloads=source_payloads,
            ticker=ticker,
            run_id=run_id,
            stage=stage,
        )

        approved_aliases = self._resolve_list(
            field="approved_aliases",
            source_payloads=source_payloads,
            alias_conflict_blacklist=alias_conflict_blacklist,
        )
        approved_historical_names = self._resolve_list(
            field="approved_historical_names",
            source_payloads=source_payloads,
            alias_conflict_blacklist=alias_conflict_blacklist,
        )

        missing_fields = self._resolve_missing_fields(
            company_name=company_name,
            industry=industry,
            approved_aliases=approved_aliases,
            approved_historical_names=approved_historical_names,
        )

        return ResolvedProfile(
            company_name=company_name,
            industry=industry,
            approved_aliases=approved_aliases,
            approved_historical_names=approved_historical_names,
            missing_fields=missing_fields,
        )

    @staticmethod
    def _load_alias_conflict_blacklist() -> set[str]:
        config = load_cn_a_news_config()
        conflicts = load_alias_conflict_blacklist(config.alias_conflict_blacklist_path)
        blacklist: set[str] = set()
        for conflict in conflicts:
            normalized_alias = ApprovedProfileResolver._normalize_non_empty_str(conflict.alias)
            if normalized_alias is not None:
                blacklist.add(normalized_alias)
        return blacklist

    def _load_source_payloads(
        self,
        *,
        ticker: str,
        run_id: str,
        stage: str,
        source_refs: dict[str, str | None],
    ) -> list[tuple[str, dict[str, Any]]]:
        source_payloads: list[tuple[str, dict[str, Any]]] = []
        for source_name, _ in _SOURCE_ORDER:
            source_ref = source_refs[source_name]
            if source_ref is None:
                continue
            payload = self._read_artifact_json(
                source_name=source_name,
                artifact_ref=source_ref,
                ticker=ticker,
                run_id=run_id,
                stage=stage,
            )
            source_payloads.append((source_name, payload))
        return source_payloads

    def _read_artifact_json(
        self,
        *,
        source_name: str,
        artifact_ref: str,
        ticker: str,
        run_id: str,
        stage: str,
    ) -> dict[str, Any]:
        artifact_path = Path(artifact_ref)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise NewsDataError(
                code=E_PROFILE_RESOLVE_FAILED,
                message=f"failed to read approved artifact: {source_name}",
                details={
                    "ticker": ticker,
                    "run_id": run_id,
                    "stage": stage,
                    "source": source_name,
                    "artifact_ref": artifact_ref,
                    "error": str(exc),
                },
            ) from exc

        if not isinstance(payload, dict):
            raise NewsDataError(
                code=E_PROFILE_RESOLVE_FAILED,
                message=f"approved artifact payload must be object: {source_name}",
                details={
                    "ticker": ticker,
                    "run_id": run_id,
                    "stage": stage,
                    "source": source_name,
                    "artifact_ref": artifact_ref,
                },
            )
        return payload

    def _resolve_scalar(
        self,
        *,
        field: str,
        source_payloads: list[tuple[str, dict[str, Any]]],
        ticker: str,
        run_id: str,
        stage: str,
    ) -> str | None:
        chosen_source: str | None = None
        chosen_value: str | None = None
        rejected_values: list[dict[str, str]] = []

        for source_name, payload in source_payloads:
            candidate_value = self._extract_scalar(payload, _SCALAR_FIELD_CANDIDATES[field])
            if candidate_value is None:
                continue
            if chosen_value is None:
                chosen_source = source_name
                chosen_value = candidate_value
                continue
            if candidate_value != chosen_value:
                rejected_values.append({"source": source_name, "value": candidate_value})

        if chosen_value is not None and rejected_values:
            _LOGGER.info(
                "profile_resolver_conflict %s",
                json.dumps(
                    {
                        "run_id": run_id,
                        "stage": stage,
                        "ticker": ticker,
                        "field": field,
                        "chosen_source": chosen_source,
                        "chosen_value": chosen_value,
                        "rejected_values": rejected_values,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        return chosen_value

    def _resolve_list(
        self,
        *,
        field: str,
        source_payloads: list[tuple[str, dict[str, Any]]],
        alias_conflict_blacklist: set[str],
    ) -> list[str]:
        values: list[str] = []
        field_candidates = _LIST_FIELD_CANDIDATES[field]
        for _, payload in source_payloads:
            for field_name in field_candidates:
                raw_value = payload.get(field_name)
                if isinstance(raw_value, list):
                    for item in raw_value:
                        normalized_item = self._normalize_non_empty_str(item)
                        if normalized_item is not None:
                            values.append(normalized_item)
        return self._normalize_unique_list(values, alias_conflict_blacklist=alias_conflict_blacklist)

    @staticmethod
    def _normalize_unique_list(values: list[str], *, alias_conflict_blacklist: set[str]) -> list[str]:
        normalized_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in alias_conflict_blacklist:
                continue
            if value in seen:
                continue
            seen.add(value)
            normalized_values.append(value)
        return normalized_values

    @staticmethod
    def _resolve_missing_fields(
        *,
        company_name: str | None,
        industry: str | None,
        approved_aliases: list[str],
        approved_historical_names: list[str],
    ) -> list[str]:
        field_values: dict[str, str | list[str] | None] = {
            "company_name": company_name,
            "industry": industry,
            "approved_aliases": approved_aliases,
            "approved_historical_names": approved_historical_names,
        }
        missing_fields: list[str] = []
        for field_name in _ALLOWED_MISSING_FIELDS:
            field_value = field_values[field_name]
            if field_value is None:
                missing_fields.append(field_name)
            elif isinstance(field_value, list) and not field_value:
                missing_fields.append(field_name)
        return missing_fields

    @staticmethod
    def _extract_scalar(payload: dict[str, Any], field_candidates: tuple[str, ...]) -> str | None:
        for field_name in field_candidates:
            normalized = ApprovedProfileResolver._normalize_non_empty_str(payload.get(field_name))
            if normalized is not None:
                return normalized
        return None

    @staticmethod
    def _normalize_non_empty_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if normalized == "":
            return None
        return normalized
