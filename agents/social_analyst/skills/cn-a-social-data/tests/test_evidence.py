from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import evidence as evidence_module  # noqa: E402

from evidence import (  # noqa: E402
    SOCIAL_EVIDENCE_TARGET_INVALID,
    SOCIAL_LOCAL_AUDIT_COPY_WRITE_FAILED,
    SOCIAL_OPENVIKING_AUTH_FAILED,
    SOCIAL_PACK_EVIDENCE_WRITE_FAILED,
    SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH,
    EvidenceWriteTarget,
    OpenVikingWriteReceipt,
    OpenVikingWriteRequest,
    OpenVikingEvidenceWriter,
    PackEvidenceWriteResult,
    RawPayloadEvidenceResult,
    SocialEvidenceError,
    build_local_audit_call_dir,
    build_openviking_l2_evidence_uri,
    canonical_json_sha256,
    validate_signal_evidence,
    try_write_raw_payload_evidence,
    write_pack_evidence,
    write_raw_payload_evidence,
)


def _target(*, call_id: str = "call-1") -> EvidenceWriteTarget:
    return EvidenceWriteTarget(
        run_id="run-20260507",
        stage="frontline",
        worker_id="social_analyst",
        call_id=call_id,
        evidence_root="/tmp/social-evidence",
    )


class _ControlledWriter(OpenVikingEvidenceWriter):
    # 仅用于单元测试错误分支控制；不是生产 OpenViking 实现。
    def __init__(self, receipt_factory) -> None:
        self._receipt_factory = receipt_factory

    def write_json(self, req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return self._receipt_factory(req)


def test_build_openviking_l2_evidence_uri_uses_runtime_evidence_branch_and_all_dimensions() -> None:
    uri = build_openviking_l2_evidence_uri(
        _target(call_id="call-abc"),
        kind="provider_raw",
        name="akshare_stock_hot_rank_latest_em_sha256_x.json",
    )
    assert uri == (
        "viking://resources/workflow/run-20260507/frontline/social_analyst/call-abc/"
        "evidence/provider_raw/akshare_stock_hot_rank_latest_em_sha256_x.json"
    )


def test_build_openviking_l2_evidence_uri_uses_configured_l2_root_when_present() -> None:
    base = _target(call_id="call-custom-root")
    target = EvidenceWriteTarget(
        run_id=base.run_id,
        stage=base.stage,
        worker_id=base.worker_id,
        call_id=base.call_id,
        evidence_root=base.evidence_root,
        openviking_l2_write_target_root="viking://resources/custom-social-root/",
    )

    uri = build_openviking_l2_evidence_uri(target, kind="pack", name="social_sentiment_pack.json")

    assert uri == (
        "viking://resources/custom-social-root/run-20260507/frontline/social_analyst/call-custom-root/"
        "evidence/pack/social_sentiment_pack.json"
    )


def test_canonical_json_sha256_is_stable_for_same_body_with_different_key_order() -> None:
    body_a = {
        "provider": "akshare",
        "payload": {"b": 2, "a": 1},
        "rows": [{"z": 2, "x": 1}],
    }
    body_b = {
        "rows": [{"x": 1, "z": 2}],
        "payload": {"a": 1, "b": 2},
        "provider": "akshare",
    }
    assert canonical_json_sha256(body_a) == canonical_json_sha256(body_b)


def test_validate_target_raises_social_evidence_target_invalid_when_call_id_missing() -> None:
    with pytest.raises(SocialEvidenceError) as exc_info:
        build_openviking_l2_evidence_uri(
            _target(call_id=""),
            kind="provider_raw",
            name="raw.json",
        )
    assert exc_info.value.code == SOCIAL_EVIDENCE_TARGET_INVALID


def test_local_audit_call_dir_is_distinct_for_different_call_id() -> None:
    path_a = build_local_audit_call_dir(_target(call_id="call-a"))
    path_b = build_local_audit_call_dir(_target(call_id="call-b"))

    assert path_a != path_b
    assert str(path_a).endswith("/run-20260507/frontline/social_analyst/call-a")
    assert str(path_b).endswith("/run-20260507/frontline/social_analyst/call-b")


def test_write_raw_payload_evidence_returns_ref_hash_and_row_count_on_success() -> None:
    raw_payload = [
        {"symbol": "600519", "heat": 99},
        {"symbol": "000001", "heat": 88},
    ]

    def _ok_receipt(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id="r-1",
            persisted_sha256=req.content_sha256,
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    evidence = write_raw_payload_evidence(
        _target(call_id="call-raw-success"),
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        raw_payload=raw_payload,
        writer=_ControlledWriter(_ok_receipt),
    )

    assert evidence.raw_payload_ref.startswith("viking://resources/workflow/run-20260507/frontline/social_analyst/")
    assert evidence.payload_hash == canonical_json_sha256(raw_payload)
    assert evidence.row_count == 2


def test_write_raw_payload_evidence_returns_hash_mismatch_error_code() -> None:
    def _bad_hash(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id="r-2",
            persisted_sha256="sha256:" + ("0" * 64),
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    result: RawPayloadEvidenceResult = try_write_raw_payload_evidence(
        _target(call_id="call-hash-mismatch"),
        provider="akshare",
        endpoint="stock_hot_keyword_em",
        raw_payload={"rows": [{"k": "白酒"}]},
        writer=_ControlledWriter(_bad_hash),
    )

    assert result.ok is False
    assert result.error_code == SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH


def test_write_raw_payload_evidence_redacts_authorization_in_local_audit_copy(tmp_path: Path) -> None:
    target = _target(call_id="call-redaction")
    target = EvidenceWriteTarget(
        run_id=target.run_id,
        stage=target.stage,
        worker_id=target.worker_id,
        call_id=target.call_id,
        evidence_root=str(tmp_path),
    )
    raw_payload = {"authorization": "Bearer secret-token", "rows": [{"symbol": "600519"}]}

    def _ok_receipt(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id="r-3",
            persisted_sha256=req.content_sha256,
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    evidence = write_raw_payload_evidence(
        target,
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        raw_payload=raw_payload,
        writer=_ControlledWriter(_ok_receipt),
    )
    local_content = Path(evidence.local_audit_path).read_text(encoding="utf-8")
    assert "Bearer secret-token" not in local_content
    assert "\"authorization\":\"***REDACTED***\"" in local_content


def test_write_raw_payload_evidence_returns_auth_failed_on_401() -> None:
    def _unauthorized(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=False,
            uri=req.uri,
            receipt_id=None,
            persisted_sha256=None,
            status_code=401,
            error_code="auth_failed",
            error_message="unauthorized",
            retryable=False,
        )

    result = try_write_raw_payload_evidence(
        _target(call_id="call-auth-failed"),
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        raw_payload=[{"symbol": "600519"}],
        writer=_ControlledWriter(_unauthorized),
    )

    assert result.ok is False
    assert result.error_code == SOCIAL_OPENVIKING_AUTH_FAILED


def test_write_pack_evidence_returns_three_l2_uris_and_final_content_hash() -> None:
    pack_body = {"schema_version": "cn_a_social_pack.v1", "ok": True, "data": {"attention_signals": []}}
    attempts = [
        {
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "raw_payload_ref": "viking://resources/workflow/run-20260507/frontline/social_analyst/call-1/evidence/provider_raw/raw-1.json",
        }
    ]
    cache_inspections = [{"endpoint": "stock_hot_rank_latest_em", "status": "hit"}]
    pack_hash = canonical_json_sha256(pack_body)

    def _ok_receipt(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id="r-pack-1",
            persisted_sha256=req.content_sha256,
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    result: PackEvidenceWriteResult = write_pack_evidence(
        _target(call_id="call-pack-success"),
        pack_body=pack_body,
        attempts=attempts,
        cache_inspections=cache_inspections,
        pack_body_hash=pack_hash,
        writer=_ControlledWriter(_ok_receipt),
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.cause_error_code is None
    assert result.evidence is not None
    assert result.evidence.pack_path.endswith("/evidence/pack/social_sentiment_pack.json")
    assert result.evidence.provider_attempts_path.endswith("/evidence/pack/provider_attempts.json")
    assert result.evidence.cache_inspection_path.endswith("/evidence/pack/cache_inspection.json")
    assert result.evidence.raw_payload_refs == [
        "viking://resources/workflow/run-20260507/frontline/social_analyst/call-1/evidence/provider_raw/raw-1.json"
    ]
    expected_content_hash = canonical_json_sha256(
        {
            "pack": pack_hash,
            "attempts": canonical_json_sha256(attempts),
            "cache": canonical_json_sha256(cache_inspections),
        }
    )
    assert result.evidence.content_hash == expected_content_hash


def test_validate_signal_evidence_returns_false_when_raw_ref_and_payload_hash_missing() -> None:
    signal = {
        "signal_id": "sig-1",
        "content_hash": "sha256:" + ("a" * 64),
        "raw_payload_ref": "",
    }
    assert validate_signal_evidence(signal) is False


def test_write_pack_evidence_returns_failed_code_when_l2_write_fails() -> None:
    def _service_error(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=False,
            uri=req.uri,
            receipt_id=None,
            persisted_sha256=None,
            status_code=503,
            error_code="service_error",
            error_message="temporarily unavailable",
            retryable=True,
        )

    result = write_pack_evidence(
        _target(call_id="call-pack-failed"),
        pack_body={"schema_version": "cn_a_social_pack.v1"},
        attempts=[],
        cache_inspections=[],
        pack_body_hash="sha256:" + ("1" * 64),
        writer=_ControlledWriter(_service_error),
    )

    assert result.ok is False
    assert result.evidence is None
    assert result.error_code == SOCIAL_PACK_EVIDENCE_WRITE_FAILED
    assert result.cause_error_code is not None


def test_write_pack_evidence_keeps_l2_uris_and_returns_warning_when_local_audit_copy_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _ok_receipt(req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id="r-pack-audit-warning",
            persisted_sha256=req.content_sha256,
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )

    def _raise_os_error(*args, **kwargs) -> None:
        raise OSError("disk read-only")

    monkeypatch.setattr(evidence_module, "_write_local_audit_copy", _raise_os_error)

    result = write_pack_evidence(
        _target(call_id="call-pack-audit-warning"),
        pack_body={"schema_version": "cn_a_social_pack.v1"},
        attempts=[],
        cache_inspections=[],
        pack_body_hash="sha256:" + ("2" * 64),
        writer=_ControlledWriter(_ok_receipt),
    )

    assert result.ok is True
    assert result.evidence is not None
    assert result.evidence.pack_path.endswith("/evidence/pack/social_sentiment_pack.json")
    assert result.evidence.provider_attempts_path.endswith("/evidence/pack/provider_attempts.json")
    assert result.evidence.cache_inspection_path.endswith("/evidence/pack/cache_inspection.json")
    assert len(result.warnings) == 3
    assert all(warning.startswith(SOCIAL_LOCAL_AUDIT_COPY_WRITE_FAILED) for warning in result.warnings)
