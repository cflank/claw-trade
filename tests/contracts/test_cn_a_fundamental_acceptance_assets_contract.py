from __future__ import annotations

import json
from pathlib import Path

TEMPLATE_PATH = Path("docs/evidence/cn_a_fundamental/fresh_final_acceptance_template.md")
MANIFEST_PATH = Path("docs/evidence/cn_a_fundamental/t_fnd_046_five_sample_manifest_2026-05-07.json")
COMPARE_INDEX_PATH = Path("docs/evidence/cn_a_fundamental/same_ticker_compare_index_600519_2026-05-06.md")
FIXTURE_PATH = Path("tests/fixtures/cn_a_fundamental/acceptance_assets_contract.json")


def test_cn_a_fundamental_acceptance_assets_contract() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    required_fields = list(fixture["required_evidence_fields"])
    required_sample_ids = list(fixture["required_sample_ids"])
    allowed_status = set(fixture["allowed_status"])
    required_compare_filenames = list(fixture["required_compare_filenames"])

    assert TEMPLATE_PATH.exists(), "fundamental fresh/final acceptance template 缺失"
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for field_name in required_fields:
        assert field_name in template_text, f"template 缺少证据字段: {field_name}"

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest.get("required_evidence_fields") == required_fields, "manifest 10 项证据字段不一致"

    samples = manifest.get("samples")
    assert isinstance(samples, list) and samples, "manifest 缺少 samples"
    sample_map: dict[str, dict[str, object]] = {}
    for sample in samples:
        assert isinstance(sample, dict), "sample 行必须是对象"
        sample_id = str(sample.get("sample_id") or "").strip()
        assert sample_id, "sample_id 不能为空"
        sample_map[sample_id] = sample

        evidence = sample.get("evidence")
        assert isinstance(evidence, dict), f"{sample_id} evidence 必须是对象"
        assert set(evidence) == set(required_fields), f"{sample_id} evidence 字段必须完整 10 项"

        for field_name in required_fields:
            slot = evidence[field_name]
            assert isinstance(slot, dict), f"{sample_id}.{field_name} 必须是对象"
            status = str(slot.get("status") or "").strip()
            path = str(slot.get("path") or "").strip()
            assert status in allowed_status, f"{sample_id}.{field_name} status 非法: {status}"
            assert path, f"{sample_id}.{field_name} path 不能为空"
            if status == "existing_real_path":
                assert Path(path).exists(), f"{sample_id}.{field_name} 标记 real 但路径不存在: {path}"

    assert set(sample_map) == set(required_sample_ids), "sample 集合不完整或存在未约定 sample_id"

    sample_5 = sample_map["sample_5"]
    compare_index = str(sample_5.get("compare_index") or "").strip()
    assert compare_index == str(COMPARE_INDEX_PATH), "sample_5 compare_index 路径不一致"
    assert COMPARE_INDEX_PATH.exists(), "same-ticker compare index 缺失"

    compare_text = COMPARE_INDEX_PATH.read_text(encoding="utf-8")
    for filename in required_compare_filenames:
        assert filename in compare_text, f"same-ticker compare index 缺少链接文件: {filename}"
