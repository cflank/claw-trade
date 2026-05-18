from __future__ import annotations

from claw_trade.data_gateway.models import Market

from tests.integration.data_gateway.test_social_pack_cn_a import assert_social_pack_contract_for_market


def test_social_pack_hk_source_role_contract() -> None:
    assert_social_pack_contract_for_market(Market.HK)
