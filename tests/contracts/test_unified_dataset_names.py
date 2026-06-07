from __future__ import annotations

import pytest
from claw_trade.data_gateway.warehouse import (
    SUPPORTED_UNIFIED_DATASETS,
    DatasetRepository,
    is_provider_style_dataset_name,
)


def test_unified_dataset_names_are_business_semantic_not_provider_semantic() -> None:
    assert "daily_bar" in SUPPORTED_UNIFIED_DATASETS
    assert "company_news" in SUPPORTED_UNIFIED_DATASETS
    assert "financial_metric" in SUPPORTED_UNIFIED_DATASETS

    for dataset in SUPPORTED_UNIFIED_DATASETS:
        assert "." not in dataset
        assert not dataset.startswith("open" + "bb_")
        assert not is_provider_style_dataset_name(dataset)


def test_repository_rejects_provider_style_dataset_names() -> None:
    repo = DatasetRepository()
    with pytest.raises(ValueError, match="unsupported dataset"):
        repo.insert_normalized(
            {
                "dataset": "tushare_daily_bar",
                "market": "CN_A",
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider": "tushare"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            }
        )
