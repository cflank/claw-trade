from __future__ import annotations

import pytest
from claw_trade.artifacts.openviking_client import OpenVikingAccessError, OpenVikingClient
from claw_trade.artifacts.refs import OpenVikingReadCapability, make_material_target
from claw_trade.workflow.models import Stage

from tests.fakes.openviking_store import FakeOpenVikingStore


def test_client_does_not_expose_latest_list_compact_or_raw_read() -> None:
    client = OpenVikingClient(FakeBackendAdapter(FakeOpenVikingStore()))
    assert not hasattr(client, "read")
    assert not hasattr(client, "read_latest")
    assert not hasattr(client, "latest_material")
    assert not hasattr(client, "list")
    assert not hasattr(client, "list_prefix")
    assert not hasattr(client, "compact_read")


def test_capability_read_rejects_raw_uri_outside_scope() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    capability = OpenVikingReadCapability(
        capability_id="cap-1",
        material_id="mat-1",
        allowed_l1_uri=target.l1_uri,
        allowed_l1_sha256="sha-l1",
        allowed_l2_prefix=target.l2_prefix,
        manifest_entry_sha256="entry-sha",
    )
    client = OpenVikingClient(FakeBackendAdapter(FakeOpenVikingStore()))
    result = client.read_with_capability(
        capability,
        "viking://resources/workflow/run-1/frontline/market_analyst/call-2/report.md",
        expected_sha256="sha-l1",
    )
    assert not result.ok
    assert result.error_category == "capability_mismatch"


def test_read_l2_index_requires_l2_capability_prefix() -> None:
    target = make_material_target("run-1", Stage.FRONTLINE, "market_analyst", "call-1")
    capability = OpenVikingReadCapability(
        capability_id="cap-1",
        material_id="mat-1",
        allowed_l1_uri=target.l1_uri,
        allowed_l1_sha256="sha-l1",
        allowed_l2_prefix=None,
        manifest_entry_sha256="entry-sha",
    )
    client = OpenVikingClient(FakeBackendAdapter(FakeOpenVikingStore()))
    with pytest.raises(OpenVikingAccessError, match="未绑定 L2 前缀"):
        client.read_l2_index_with_capability(capability)


class FakeBackendAdapter:
    def __init__(self, store: FakeOpenVikingStore) -> None:
        self._store = store

    def ensure_namespace(self, namespace: str) -> None:
        if not namespace:
            raise OpenVikingAccessError("namespace 不能为空", category="invalid_uri")

    def fetch_receipt_by_path(self, receipt_path):  # type: ignore[no-untyped-def]
        return self._store.read_receipt(receipt_path)

    def fetch_stat_by_uri(self, uri: str):  # type: ignore[no-untyped-def]
        return self._store.stat(uri)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self._store.read(uri)

    def fetch_l2_index_by_uri(self, uri: str | None):
        return self._store.read_l2_index(uri)
