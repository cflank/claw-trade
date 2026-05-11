from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

CLAIM_RULES_VERSION = "fundamental_claim_rules.v1"
_CLAIM_DICTIONARY_PATH = Path(__file__).with_name("fundamental_claim_dictionaries_v1.json")


def _load_claim_dictionary_asset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("fundamental claim dictionary asset must be a JSON object")
    return payload


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise RuntimeError(f"fundamental claim dictionary asset invalid field: {field}")
    return value.strip()


def _require_text_list(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"fundamental claim dictionary asset invalid list field: {field}")
    texts = tuple(_require_text(item, field=field) for item in value)
    if len(texts) == 0:
        raise RuntimeError(f"fundamental claim dictionary asset list empty: {field}")
    return texts


def _require_pattern_dict(value: Any, *, field: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"fundamental claim dictionary asset invalid map field: {field}")
    result: dict[str, tuple[str, ...]] = {}
    for key, patterns in value.items():
        key_text = _require_text(key, field=field)
        result[key_text] = _require_text_list(patterns, field=f"{field}.{key_text}")
    if len(result) == 0:
        raise RuntimeError(f"fundamental claim dictionary asset map empty: {field}")
    return result


def _require_field_path_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RuntimeError("fundamental claim dictionary asset invalid field: metric_field_paths")
    result: dict[str, str] = {}
    for key, field_path in value.items():
        key_text = _require_text(key, field="metric_field_paths")
        result[key_text] = _require_text(field_path, field=f"metric_field_paths.{key_text}")
    if len(result) == 0:
        raise RuntimeError("fundamental claim dictionary asset metric_field_paths empty")
    return result


_ASSET = _load_claim_dictionary_asset(_CLAIM_DICTIONARY_PATH)

CLAIM_DICTIONARY_REVISION_ID = _require_text(
    _ASSET.get("dictionary_revision_id"),
    field="dictionary_revision_id",
)
METRIC_FIELD_PATHS = _require_field_path_map(_ASSET.get("metric_field_paths"))
METRIC_PATTERNS = _require_pattern_dict(_ASSET.get("metric_keywords_v1"), field="metric_keywords_v1")
CONCLUSION_PATTERNS = _require_pattern_dict(_ASSET.get("conclusion_phrases_v1"), field="conclusion_phrases_v1")
NARRATIVE_PATTERNS = _require_pattern_dict(_ASSET.get("narrative_phrases_v1"), field="narrative_phrases_v1")
TREND_KEYWORDS = _require_text_list(_ASSET.get("trend_keywords_v1"), field="trend_keywords_v1")
COMPLIANT_DOWNGRADE_PHRASES = _require_text_list(
    _ASSET.get("compliant_downgrade_phrases_v1"),
    field="compliant_downgrade_phrases_v1",
)
