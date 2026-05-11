from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from guard import (  # noqa: E402
    SOCIAL_FAILED_PACK_OVERSTATED,
    SOCIAL_FORBIDDEN_SOURCE_USED,
    SOCIAL_PROVIDER_ATTEMPTS_MISSING,
    SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM,
    SOCIAL_SIGNAL_EVIDENCE_MISSING,
    validate_social_pack_schema,
    validate_social_report_against_pack,
)


def test_validate_social_pack_schema_returns_provider_attempts_missing_when_pack_lacks_attempts() -> None:
    pack = _load_valid_pack_payload()
    pack.pop("provider_attempts", None)

    result = validate_social_pack_schema(pack)

    assert result.ok is False
    assert SOCIAL_PROVIDER_ATTEMPTS_MISSING in result.reason_codes


def test_validate_social_pack_schema_rejects_forbidden_source_endpoint() -> None:
    pack = _load_valid_pack_payload()
    pack["provider_attempts"][0]["endpoint"] = "stock_hot_follow_xq"

    result = validate_social_pack_schema(pack)

    assert result.ok is False
    assert SOCIAL_FORBIDDEN_SOURCE_USED in result.reason_codes


def test_validate_social_pack_schema_rejects_missing_evidence_refs_for_success_attempt() -> None:
    pack = _load_valid_pack_payload()
    pack["provider_attempts"][0]["raw_payload_ref"] = None

    result = validate_social_pack_schema(pack)

    assert result.ok is False
    assert SOCIAL_SIGNAL_EVIDENCE_MISSING in result.reason_codes


def test_validate_social_report_against_pack_rejects_failed_pack_overstated_report() -> None:
    pack = _load_valid_pack_payload()
    pack["ok"] = False
    pack["quality"]["status"] = "failed"
    report = "本期投资者情绪明显偏多，情绪评分：8分。"

    result = validate_social_report_against_pack(report, pack)

    assert result.ok is False
    assert SOCIAL_FAILED_PACK_OVERSTATED in result.reason_codes


def test_validate_social_report_against_pack_rejects_unsupported_source_claim() -> None:
    pack = _load_valid_pack_payload()
    report = "我们已读取平台原帖正文、KOL观点与散户/机构分层样本，并据此确认用户观点分歧。"

    result = validate_social_report_against_pack(report, pack)

    assert result.ok is False
    assert SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM in result.reason_codes


def _load_valid_pack_payload() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "cn_a_social_pack_v1_contract.json"
    return json.loads(fixture.read_text(encoding="utf-8"))
