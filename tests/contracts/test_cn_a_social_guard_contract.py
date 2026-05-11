from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_GUARD_PATH = SOCIAL_SCRIPTS_ROOT / "guard.py"
SOCIAL_PACK_FIXTURE_PATH = Path(
    "agents/social_analyst/skills/cn-a-social-data/tests/fixtures/cn_a_social_pack_v1_contract.json"
)


def test_validate_social_pack_schema_rejects_invalid_schema_version() -> None:
    pack = _load_valid_pack_payload()
    pack["schema_version"] = "cn_a_social_pack.v0"

    result = GUARD_MODULE.validate_social_pack_schema(pack)

    assert result.ok is False
    assert GUARD_MODULE.SOCIAL_PACK_SCHEMA_INVALID in result.reason_codes


def test_validate_social_pack_schema_rejects_forbidden_provider_source() -> None:
    pack = _load_valid_pack_payload()
    pack["provider_attempts"][0]["endpoint"] = "stock_hot_follow_xq"

    result = GUARD_MODULE.validate_social_pack_schema(pack)

    assert result.ok is False
    assert GUARD_MODULE.SOCIAL_FORBIDDEN_SOURCE_USED in result.reason_codes


def test_validate_social_pack_schema_rejects_missing_evidence_refs_for_success_attempt() -> None:
    pack = _load_valid_pack_payload()
    pack["provider_attempts"][0]["raw_payload_ref"] = None

    result = GUARD_MODULE.validate_social_pack_schema(pack)

    assert result.ok is False
    assert GUARD_MODULE.SOCIAL_SIGNAL_EVIDENCE_MISSING in result.reason_codes


def test_validate_social_report_against_pack_rejects_unsupported_source_claim() -> None:
    pack = _load_valid_pack_payload()
    report = "我们已读取平台原帖正文、KOL观点与散户/机构分层样本，并据此确认用户观点分歧。"

    result = GUARD_MODULE.validate_social_report_against_pack(report, pack)

    assert result.ok is False
    assert GUARD_MODULE.SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM in result.reason_codes


def _load_valid_pack_payload() -> dict[str, object]:
    return json.loads(SOCIAL_PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_guard_module():
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    module_name = "cn_a_social_guard_contract"
    spec = importlib.util.spec_from_file_location(module_name, SOCIAL_GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 guard 模块: {SOCIAL_GUARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


GUARD_MODULE = _load_guard_module()
