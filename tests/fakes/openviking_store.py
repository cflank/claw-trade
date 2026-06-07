from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from claw_trade.artifacts.manifest import OpenVikingReadCapability
from claw_trade.artifacts.openviking_client import (
    OpenVikingAccessError,
    OpenVikingApprovalClient,
    OpenVikingStat,
)
from claw_trade.artifacts.refs import L2Index, MaterialReceipt, MaterialTarget, VikingUri


class FakeOpenVikingStore(OpenVikingApprovalClient):
    def __init__(self) -> None:
        self._objects: dict[VikingUri, bytes] = {}
        self._stats: dict[VikingUri, OpenVikingStat] = {}
        self._l2_index_by_uri: dict[VikingUri, L2Index] = {}
        self._receipts: dict[Path, MaterialReceipt] = {}

    def seed_object(self, uri: VikingUri, content: bytes | str) -> OpenVikingStat:
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        stat = OpenVikingStat(
            uri=uri,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        self._objects[uri] = data
        self._stats[uri] = stat
        return stat

    def seed_stat(self, uri: VikingUri, size_bytes: int, sha256: str) -> OpenVikingStat:
        stat = OpenVikingStat(uri=uri, size_bytes=size_bytes, sha256=sha256)
        self._stats[uri] = stat
        return stat

    def seed_l2_index(self, uri: VikingUri, index: L2Index) -> None:
        self._l2_index_by_uri[uri] = index

    def seed_receipt(self, receipt_path: Path, receipt: MaterialReceipt) -> None:
        self._receipts[receipt_path] = receipt

    def read(self, uri: VikingUri) -> bytes:
        if uri not in self._objects:
            raise OpenVikingAccessError(f"未找到 URI: {uri}")
        return self._objects[uri]

    def read_l2_index(self, uri: VikingUri | None) -> L2Index:
        if uri is None:
            return L2Index(
                entries=(),
                empty_reason="missing_l2_index_uri",
                index_uri=None,
                index_sha256=None,
                index_size_bytes=None,
            )
        if uri not in self._l2_index_by_uri:
            raise OpenVikingAccessError(f"未找到 L2 索引: {uri}")
        return self._l2_index_by_uri[uri]

    def stat(self, uri: VikingUri) -> OpenVikingStat:
        if uri in self._stats:
            return self._stats[uri]
        if uri in self._objects:
            data = self._objects[uri]
            return OpenVikingStat(uri=uri, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        raise OpenVikingAccessError(f"未找到 stat: {uri}")

    def read_receipt(self, receipt_path: Path) -> MaterialReceipt:
        if receipt_path not in self._receipts:
            raise OpenVikingAccessError(f"未找到回执: {receipt_path}")
        return self._receipts[receipt_path]

    def read_latest(self, uri_prefix: VikingUri) -> bytes:
        raise OpenVikingAccessError(f"禁止 latest read: {uri_prefix}")

    def list_prefix(self, uri_prefix: VikingUri) -> tuple[VikingUri, ...]:
        raise OpenVikingAccessError(f"禁止 list: {uri_prefix}")

    def compact_read(self, capability: OpenVikingReadCapability) -> str:
        raise OpenVikingAccessError(f"禁止 compact read: {capability.capability_id}")

    def write_compact_as_l1(self, target: MaterialTarget, compact_text: str) -> None:
        raise OpenVikingAccessError(f"禁止 compact write: {target.l1_uri}")


class FakeOpenVikingWorkerTools:
    def __init__(self, store: FakeOpenVikingStore | None = None) -> None:
        self._store = store or FakeOpenVikingStore()

    @property
    def store(self) -> FakeOpenVikingStore:
        return self._store

    def read(self, uri: VikingUri) -> bytes:
        raise OpenVikingAccessError(f"worker 禁止裸 URI read: {uri}")

    def read_with_capability(
        self,
        capability: OpenVikingReadCapability,
        *,
        layer: str = "l1",
        uri: VikingUri | None = None,
    ) -> bytes:
        if layer == "l1":
            return self._store.read(capability.allowed_l1_uri)
        if layer == "l2":
            if uri is None:
                raise OpenVikingAccessError("L2 read 缺少 URI")
            allowed_prefix = capability.allowed_l2_prefix
            if not allowed_prefix or not uri.startswith(allowed_prefix):
                raise OpenVikingAccessError(f"L2 URI 越权: {uri}")
            return self._store.read(uri)
        raise OpenVikingAccessError(f"不支持的读取层: {layer}")

    def write_l1_target(self, target: MaterialTarget, *, uri: VikingUri, content: str | bytes) -> MaterialReceipt:
        if uri != target.l1_uri:
            raise OpenVikingAccessError(f"L1 写入越权: {uri}")
        stat = self._store.seed_object(uri, content)
        return MaterialReceipt(
            uri=uri,
            run_id=target.run_id,
            call_id=target.call_id,
            worker_id=target.worker_id,
            stage=target.stage,
            target_name=target.target_name,
            sha256=stat.sha256,
            size_bytes=stat.size_bytes,
            written_at="fake-runtime",
            receipt_id=None,
        )

    def write_l2_entry(self, target: MaterialTarget, *, uri: VikingUri, content: bytes | str) -> OpenVikingStat:
        if not uri.startswith(target.l2_prefix):
            raise OpenVikingAccessError(f"L2 写入越权: {uri}")
        return self._store.seed_object(uri, content)

    def read_latest(self, uri_prefix: VikingUri) -> bytes:
        raise OpenVikingAccessError(f"worker 禁止 latest read: {uri_prefix}")

    def list_prefix(self, uri_prefix: VikingUri) -> tuple[VikingUri, ...]:
        raise OpenVikingAccessError(f"worker 禁止 list: {uri_prefix}")

    def compact_read(self, capability: OpenVikingReadCapability) -> str:
        raise OpenVikingAccessError(f"worker 禁止 compact read: {capability.capability_id}")

    def write_compact_as_l1(self, target: MaterialTarget, compact_text: str) -> None:
        raise OpenVikingAccessError(f"worker 禁止 compact write: {target.l1_uri}")

    def overwrite_receipt_target(self, receipt: MaterialReceipt, **changes: Any) -> MaterialReceipt:
        return replace(receipt, **changes)
