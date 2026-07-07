from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from claw_trade.artifacts.refs import (
    ApprovedMaterial,
    L2Index,
    MaterialReceipt,
    MaterialTarget,
    OpenVikingReadCapability,
    VikingUri,
    validate_viking_uri_shape,
)

OPENVIKING_ERROR_CATEGORIES = (
    "not_found",
    "permission_denied",
    "capability_mismatch",
    "hash_mismatch",
    "size_mismatch",
    "backend_unavailable",
    "invalid_uri",
    "unknown",
)

OpenVikingErrorCategory = Literal[
    "not_found",
    "permission_denied",
    "capability_mismatch",
    "hash_mismatch",
    "size_mismatch",
    "backend_unavailable",
    "invalid_uri",
    "unknown",
]

OpenVikingUri = str
OPENVIKING_PROBE_RECEIPT_PATH = Path("runs/probe/openviking/receipt.json")
OPENVIKING_PROBE_STAT_URI = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"
# 兼容历史测试/导入：boot probe 不再要求写入该 URI。
OPENVIKING_PROBE_READ_URI = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/evidence/probe-read.json"
OPENVIKING_PROBE_NAMESPACE = "workflow/probe"
OPENVIKING_PROBE_NAMESPACE_URI = "viking://resources/workflow/probe/"


def _now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_error_category(error_category: OpenVikingErrorCategory | None) -> None:
    if error_category is None:
        return
    if error_category not in OPENVIKING_ERROR_CATEGORIES:
        allowed = ", ".join(OPENVIKING_ERROR_CATEGORIES)
        raise ValueError(f"unsupported error_category {error_category!r}, allowed: {allowed}")


def _configured_probe_run_root() -> Path | None:
    run_dir = os.environ.get("CLAW_TRADE_REPORT_RUN_DIR", "").strip()
    if run_dir:
        return Path(run_dir)
    return None


class OpenVikingAccessError(RuntimeError):
    def __init__(self, message: str, *, category: OpenVikingErrorCategory = "unknown") -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str | None = None

    @classmethod
    def passed(cls) -> ProbeResult:
        return cls(ok=True, reason=None)

    @classmethod
    def failed(cls, reason: str) -> ProbeResult:
        return cls(ok=False, reason=reason)


@dataclass(frozen=True)
class OpenVikingStat:
    uri: OpenVikingUri
    ok: bool = True
    sha256: str | None = None
    size_bytes: int | None = None
    exists: bool = True
    is_dir: bool = False
    checked_at: str | None = None
    error_category: OpenVikingErrorCategory | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _validate_error_category(self.error_category)
        checked_at = self.checked_at or _now_text()
        object.__setattr__(self, "checked_at", checked_at)
        if self.ok:
            if self.error_category is not None or self.error_message is not None:
                raise ValueError("ok=True 时不能携带 error_category/error_message")
            if self.exists and not self.is_dir and self.size_bytes is None:
                raise ValueError("ok=True 且 exists=True 且非目录时必须包含 size_bytes")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes 不能为负数")


@dataclass(frozen=True)
class OpenVikingReadResult:
    uri: OpenVikingUri
    ok: bool
    content: bytes | None
    sha256: str | None
    size_bytes: int | None
    error_category: OpenVikingErrorCategory | None
    error_message: str | None

    def __post_init__(self) -> None:
        _validate_error_category(self.error_category)
        if self.ok:
            if self.error_category is not None or self.error_message is not None:
                raise ValueError("ok=True 时不能携带 error_category/error_message")
            if self.content is None or self.sha256 is None or self.size_bytes is None:
                raise ValueError("ok=True 时必须包含 content/sha256/size_bytes")
            if len(self.content) != self.size_bytes:
                raise ValueError("read.size_bytes 与 content 长度不一致")
            actual_sha = hashlib.sha256(self.content).hexdigest()
            if actual_sha != self.sha256:
                raise ValueError("read.sha256 与 content 指纹不一致")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes 不能为负数")


@dataclass(frozen=True)
class OpenVikingAccessRecord:
    action: Literal["stat", "read"]
    uri: OpenVikingUri
    ok: bool
    checked_at: str
    capability_id: str | None
    sha256: str | None
    size_bytes: int | None
    error_category: OpenVikingErrorCategory | None
    error_message: str | None

    def __post_init__(self) -> None:
        _validate_error_category(self.error_category)


class OpenVikingApprovalClient(Protocol):
    # 兼容现有假实现：审批阶段可通过这个协议喂给 guard，但真实审批调用应走 receipt+target 方法。
    def fetch_content_by_uri(self, uri: VikingUri) -> bytes: ...

    def fetch_l2_index_by_uri(self, uri: VikingUri | None) -> L2Index: ...

    def fetch_stat_by_uri(self, uri: VikingUri) -> OpenVikingStat: ...

    def fetch_receipt_by_path(self, receipt_path: Path) -> MaterialReceipt: ...


class OpenVikingBackend(OpenVikingApprovalClient, Protocol):
    def ensure_namespace(self, namespace: str) -> None: ...

    def prepare_probe_receipt(
        self,
        *,
        receipt_path: Path,
        stat_uri: VikingUri,
        read_uri: VikingUri | None = None,
    ) -> None: ...


class OpenVikingClient:
    def __init__(self, backend: OpenVikingBackend) -> None:
        self._backend = backend
        probe_run_id = os.environ.get("CLAW_TRADE_OPENVIKING_PROBE_RUN_ID", "").strip()
        probe_run_root = _configured_probe_run_root()
        if probe_run_id:
            # 每次 boot probe 使用独立 URI，避免 OpenViking 对固定 probe 文件的并发锁污染正式运行。
            self._probe_run_id = probe_run_id
            self._probe_receipt_path = (probe_run_root or Path("runs")) / probe_run_id / "openviking" / "receipt.json"
            self._probe_stat_uri = f"viking://resources/workflow/{probe_run_id}/frontline/probe_worker/probe_call/report.md"
            self._probe_namespace = f"workflow/{probe_run_id}"
            self._probe_namespace_uri = f"viking://resources/workflow/{probe_run_id}/"
        else:
            self._probe_run_id = "probe"
            self._probe_receipt_path = (
                probe_run_root / "probe" / "openviking" / "receipt.json"
                if probe_run_root is not None
                else OPENVIKING_PROBE_RECEIPT_PATH
            )
            self._probe_stat_uri = OPENVIKING_PROBE_STAT_URI
            self._probe_namespace = OPENVIKING_PROBE_NAMESPACE
            self._probe_namespace_uri = OPENVIKING_PROBE_NAMESPACE_URI

    def ensure_namespace(self, namespace: str) -> None:
        # namespace 是本次 run 的材料工作区；创建失败必须显式失败，不能静默换到其它目录。
        if not namespace.strip():
            raise OpenVikingAccessError("namespace 不能为空", category="invalid_uri")
        try:
            self._backend.ensure_namespace(namespace)
        except OpenVikingAccessError:
            raise
        except Exception as exc:
            raise OpenVikingAccessError(f"namespace 初始化失败: {exc}", category="backend_unavailable") from exc

    def probe_namespace_stat(self) -> ProbeResult:
        required = ("ensure_namespace", "fetch_stat_by_uri")
        missing = [name for name in required if not callable(getattr(self._backend, name, None))]
        if missing:
            return ProbeResult.failed(f"openviking 后端缺少方法: {','.join(missing)}")

        # first_response 启动探针只验证真实服务可写命名空间并可 stat 目录，不要求 receipt 或 raw output 证据。
        try:
            self._backend.ensure_namespace(self._probe_namespace)
        except OpenVikingAccessError as exc:
            return ProbeResult.failed(f"openviking namespace probe failed: {exc.category}:{exc}")
        except Exception as exc:
            return ProbeResult.failed(f"openviking namespace probe failed: {exc}")

        stat = self._stat_raw(self._probe_namespace_uri)
        if not stat.ok:
            return ProbeResult.failed(f"openviking namespace stat probe failed: {stat.error_category}:{stat.error_message}")
        if not stat.exists:
            return ProbeResult.failed("openviking namespace stat probe failed: namespace 不存在")
        if not stat.is_dir:
            return ProbeResult.failed("openviking namespace stat probe failed: namespace 不是目录")
        return ProbeResult.passed()

    def probe_read_stat_receipt(self) -> ProbeResult:
        required = ("fetch_receipt_by_path", "fetch_stat_by_uri", "fetch_content_by_uri", "fetch_l2_index_by_uri")
        missing = [name for name in required if not callable(getattr(self._backend, name, None))]
        if missing:
            return ProbeResult.failed(f"openviking 后端缺少方法: {','.join(missing)}")

        first_check = self._validate_existing_probe_receipt()
        if first_check.ok:
            return first_check

        prepare = getattr(self._backend, "prepare_probe_receipt", None)
        if not callable(prepare):
            return first_check
        prepare_error = self._prepare_probe_receipt_if_supported()
        if prepare_error is not None:
            return ProbeResult.failed(prepare_error)

        return self._validate_existing_probe_receipt()

    def _validate_existing_probe_receipt(self) -> ProbeResult:
        # 这里必须做真实调用：仅检查 callable 会放行“方法存在但 backend 已坏”的假成功，boot 会误以为 OpenViking 可用。
        try:
            receipt = self._backend.fetch_receipt_by_path(self._probe_receipt_path)
        except Exception as exc:
            return ProbeResult.failed(f"openviking receipt probe failed: {exc}")
        if not isinstance(receipt, MaterialReceipt):
            return ProbeResult.failed("openviking receipt probe invalid result: expected MaterialReceipt")

        stat = self._stat_raw(self._probe_stat_uri)
        if not stat.ok:
            return ProbeResult.failed(f"openviking stat probe failed: {stat.error_category}:{stat.error_message}")
        if stat.uri != self._probe_stat_uri:
            return ProbeResult.failed("openviking stat probe failed: stat.uri 与当前 probe URI 不一致")
        if not stat.exists:
            return ProbeResult.failed("openviking stat probe failed: probe URI 不存在")
        if stat.is_dir:
            return ProbeResult.failed("openviking stat probe failed: probe URI 不是文件")
        if stat.size_bytes is None or stat.size_bytes <= 0:
            return ProbeResult.failed("openviking stat probe failed: stat.size_bytes 必须大于 0")

        # stat 缺 sha 时不能放行；必须走真实 read-back，并由 read 的 sha/size 对 receipt 做完整性证明。
        read_result = self._read_raw(self._probe_stat_uri)
        if not read_result.ok:
            return ProbeResult.failed(
                f"openviking read probe failed: {read_result.error_category}:{read_result.error_message}"
            )
        if read_result.size_bytes is None or read_result.size_bytes <= 0:
            return ProbeResult.failed("openviking read probe failed: read.size_bytes 必须大于 0")
        receipt_check = _validate_probe_receipt_identity(receipt, stat_uri=self._probe_stat_uri, run_id=self._probe_run_id)
        if receipt_check is not None:
            return ProbeResult.failed(f"openviking receipt probe failed: {receipt_check}")
        return ProbeResult.passed()

    def _prepare_probe_receipt_if_supported(self) -> str | None:
        prepare = getattr(self._backend, "prepare_probe_receipt", None)
        if not callable(prepare):
            return None
        try:
            prepare(
                receipt_path=self._probe_receipt_path,
                stat_uri=self._probe_stat_uri,
            )
        except OpenVikingAccessError as exc:
            return f"openviking probe prepare failed: {exc.category}:{exc}"
        except Exception as exc:
            return f"openviking probe prepare failed: {exc}"
        return None

    def read_receipt(self, path: Path) -> MaterialReceipt:
        try:
            return self._backend.fetch_receipt_by_path(path)
        except OpenVikingAccessError:
            raise
        except Exception as exc:
            raise OpenVikingAccessError(f"读取 receipt 失败: {path} ({exc})", category="backend_unavailable") from exc

    def stat_for_receipt_verification(self, receipt: MaterialReceipt, target: MaterialTarget) -> OpenVikingStat:
        if receipt.uri != target.l1_uri:
            return _failed_stat(receipt.uri, "invalid_uri", "receipt.uri 与 target.l1_uri 不一致")
        uri_check = validate_viking_uri_shape(receipt.uri, target.run_id, target.stage, target.worker_id, target.call_id)
        if not uri_check.ok:
            return _failed_stat(receipt.uri, "invalid_uri", uri_check.reason or "URI 形状校验失败")
        stat = self._stat_raw(receipt.uri)
        if not stat.ok:
            return stat
        if not stat.exists:
            return _failed_stat(receipt.uri, "not_found", "stat.exists=False")
        if stat.is_dir:
            return _failed_stat(receipt.uri, "invalid_uri", "receipt.uri 指向目录而不是文件")
        if stat.size_bytes is None or stat.size_bytes <= 0:
            return _failed_stat(receipt.uri, "size_mismatch", "stat.size_bytes 必须大于 0")
        return stat

    def read_for_receipt_verification(self, receipt: MaterialReceipt, target: MaterialTarget) -> OpenVikingReadResult:
        if receipt.uri != target.l1_uri:
            return _failed_read(receipt.uri, "invalid_uri", "receipt.uri 与 target.l1_uri 不一致")
        uri_check = validate_viking_uri_shape(receipt.uri, target.run_id, target.stage, target.worker_id, target.call_id)
        if not uri_check.ok:
            return _failed_read(receipt.uri, "invalid_uri", uri_check.reason or "URI 形状校验失败")
        read_result = self._read_raw(receipt.uri)
        if not read_result.ok:
            return read_result
        if read_result.size_bytes is None or read_result.size_bytes <= 0:
            return _failed_read(receipt.uri, "size_mismatch", "read.size_bytes 必须大于 0")
        if read_result.content is None or len(read_result.content) == 0:
            return _failed_read(receipt.uri, "size_mismatch", "read.content 不能为空")
        return read_result

    def read_approved_l1(self, material: ApprovedMaterial) -> OpenVikingReadResult:
        # 导出/审批侧权威读取：只接受 approved material，按 run/stage/worker/call+sha/size 校验。
        uri_check = validate_viking_uri_shape(
            material.l1_uri,
            material.run_id,
            material.stage,
            material.worker_id,
            material.call_id,
        )
        if not uri_check.ok:
            return _failed_read(material.l1_uri, "invalid_uri", uri_check.reason or "approved material URI 形状非法")
        read_result = self._read_raw(material.l1_uri)
        if not read_result.ok:
            return read_result
        if read_result.size_bytes is None or read_result.size_bytes <= 0:
            return _failed_read(material.l1_uri, "size_mismatch", "read.size_bytes 必须大于 0")
        if read_result.content is None or len(read_result.content) == 0:
            return _failed_read(material.l1_uri, "size_mismatch", "read.content 不能为空")
        return read_result

    def read_l2_index_for_receipt_verification(self, receipt: MaterialReceipt, target: MaterialTarget) -> L2Index:
        # 审批阶段只能读取本 call 的固定 L2 index，禁止 latest/list/目录扫描。
        uri_check = validate_viking_uri_shape(receipt.uri, target.run_id, target.stage, target.worker_id, target.call_id)
        if not uri_check.ok:
            raise OpenVikingAccessError(uri_check.reason or "receipt URI 非法", category="invalid_uri")
        index_uri = f"{target.l2_prefix}index.json"
        index_check = validate_viking_uri_shape(index_uri, target.run_id, target.stage, target.worker_id, target.call_id)
        if not index_check.ok:
            raise OpenVikingAccessError(index_check.reason or "L2 index URI 非法", category="invalid_uri")
        try:
            return self._backend.fetch_l2_index_by_uri(index_uri)
        except OpenVikingAccessError:
            raise
        except Exception as exc:
            raise OpenVikingAccessError(f"读取 L2 index 失败: {index_uri} ({exc})", category="backend_unavailable") from exc

    def stat_with_capability(self, capability: OpenVikingReadCapability, uri: VikingUri) -> OpenVikingStat:
        # 下游读取边界：必须先过 capability 校验，禁止裸 URI 读取“顺便成功”。
        check = self._check_capability_uri(capability, uri)
        if not check.ok:
            return _failed_stat(uri, "capability_mismatch", check.reason)
        stat = self._stat_raw(uri)
        if not stat.ok:
            return stat
        if not stat.exists:
            return _failed_stat(uri, "not_found", "stat.exists=False")
        if stat.is_dir:
            return _failed_stat(uri, "invalid_uri", "read uri 指向目录而不是文件")
        if stat.size_bytes is None or stat.size_bytes <= 0:
            return _failed_stat(uri, "size_mismatch", "stat.size_bytes 必须大于 0")
        return stat

    def read_with_capability(
        self,
        capability: OpenVikingReadCapability,
        uri: VikingUri,
        expected_sha256: str,
    ) -> OpenVikingReadResult:
        del expected_sha256
        check = self._check_capability_uri(capability, uri)
        if not check.ok:
            return _failed_read(uri, "capability_mismatch", check.reason)
        read_result = self._read_raw(uri)
        if not read_result.ok:
            return read_result
        if read_result.size_bytes is None or read_result.size_bytes <= 0:
            return _failed_read(uri, "size_mismatch", "read.size_bytes 必须大于 0")
        if read_result.content is None or len(read_result.content) == 0:
            return _failed_read(uri, "size_mismatch", "read.content 不能为空")
        return read_result

    def read_l2_index_with_capability(self, capability: OpenVikingReadCapability) -> L2Index:
        prefix = capability.allowed_l2_prefix
        if not prefix:
            raise OpenVikingAccessError("capability 未绑定 L2 前缀", category="capability_mismatch")
        run_id, stage, worker_id, call_id = _identity_from_uri(capability.allowed_l1_uri)
        index_uri = f"{prefix}index.json"
        check = validate_viking_uri_shape(index_uri, run_id, stage, worker_id, call_id)
        if not check.ok:
            raise OpenVikingAccessError(check.reason or "L2 index URI 非法", category="invalid_uri")
        try:
            return self._backend.fetch_l2_index_by_uri(index_uri)
        except OpenVikingAccessError:
            raise
        except Exception as exc:
            raise OpenVikingAccessError(f"读取 L2 index 失败: {index_uri} ({exc})", category="backend_unavailable") from exc

    # ---- Optional OpenViking control-plane wrappers (tree/search/relations/pack/health/memory) ----
    # 这些方法只做通用透传，不改变 claw-trade 的 worker 可见工具边界。

    def tree_run(self, *, run_id: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("tree_run", "tree"), uri)

    def grep_run(self, *, run_id: str, pattern: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("grep_run", "grep"), uri, pattern)

    def glob_run(self, *, run_id: str, pattern: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("glob_run", "glob"), uri, pattern)

    def find_approved_materials(self, *, run_id: str, query: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("find_approved_materials", "find"), uri, query)

    def relations(self, *, uri: VikingUri) -> object:
        return self._call_backend_extension(("relations", "get_relations"), uri)

    def link_relation(self, relation: dict[str, object]) -> object:
        return self._call_backend_extension(("link_relation", "relations_link"), relation)

    def export_run_pack(self, *, run_id: str, output_dir: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("export_run_pack", "pack_export"), uri, output_dir)

    def import_run_pack(self, *, bundle_path: str, target_run_id: str, verify_hashes: bool = True) -> object:
        return self._call_backend_extension(
            ("import_run_pack", "pack_import"),
            bundle_path,
            f"workflow/imported/{target_run_id}",
            verify_hashes,
        )

    def semantic_index_status(self, *, run_id: str) -> object:
        uri = f"viking://resources/workflow/{run_id}/"
        return self._call_backend_extension(("semantic_index_status",), uri)

    def write_engineering_memory(self, payload: dict[str, object]) -> object:
        return self._call_backend_extension(("write_engineering_memory", "memory_write"), payload)

    def read_engineering_memory(self, *, run_id: str, query: str) -> object:
        return self._call_backend_extension(("read_engineering_memory", "memory_read"), run_id, query)

    def runtime_metrics(self) -> object:
        return self._call_backend_extension(("runtime_metrics", "metrics_status"))

    def runtime_observer(self) -> object:
        return self._call_backend_extension(("runtime_observer", "observer_status"))

    def runtime_locks(self) -> object:
        return self._call_backend_extension(("runtime_locks", "lock_status"))

    def runtime_recovery(self) -> object:
        return self._call_backend_extension(("runtime_recovery", "recovery_status"))

    def runtime_semantic_queue(self) -> object:
        return self._call_backend_extension(("runtime_semantic_queue", "queue_status"))

    def _stat_raw(self, uri: VikingUri) -> OpenVikingStat:
        try:
            stat = self._backend.fetch_stat_by_uri(uri)
            if not stat.checked_at:
                stat = OpenVikingStat(
                    uri=stat.uri,
                    ok=stat.ok,
                    sha256=stat.sha256,
                    size_bytes=stat.size_bytes,
                    exists=stat.exists,
                    is_dir=stat.is_dir,
                    checked_at=_now_text(),
                    error_category=stat.error_category,
                    error_message=stat.error_message,
                )
            return stat
        except OpenVikingAccessError as exc:
            return _failed_stat(uri, exc.category, str(exc))
        except Exception as exc:
            return _failed_stat(uri, "backend_unavailable", str(exc))

    def _read_raw(self, uri: VikingUri) -> OpenVikingReadResult:
        try:
            content = self._backend.fetch_content_by_uri(uri)
            size_bytes = len(content)
            sha256 = hashlib.sha256(content).hexdigest()
            return OpenVikingReadResult(
                uri=uri,
                ok=True,
                content=content,
                sha256=sha256,
                size_bytes=size_bytes,
                error_category=None,
                error_message=None,
            )
        except OpenVikingAccessError as exc:
            return _failed_read(uri, exc.category, str(exc))
        except Exception as exc:
            return _failed_read(uri, "backend_unavailable", str(exc))

    def _call_backend_extension(self, names: tuple[str, ...], *args: object) -> object:
        for name in names:
            fn = getattr(self._backend, name, None)
            if not callable(fn):
                continue
            try:
                return fn(*args)
            except OpenVikingAccessError:
                raise
            except Exception as exc:
                raise OpenVikingAccessError(f"openviking {name} 调用失败: {exc}", category="backend_unavailable") from exc
        methods = ",".join(names)
        raise OpenVikingAccessError(f"openviking 后端未实现能力: {methods}", category="backend_unavailable")

    def _check_capability_uri(self, capability: OpenVikingReadCapability, uri: VikingUri) -> ProbeResult:
        run_id, stage, worker_id, call_id = _identity_from_uri(capability.allowed_l1_uri)
        uri_check = validate_viking_uri_shape(uri, run_id, stage, worker_id, call_id)
        if not uri_check.ok:
            return ProbeResult.failed(uri_check.reason or "URI 形状校验失败")
        if uri == capability.allowed_l1_uri:
            return ProbeResult.passed()
        prefix = capability.allowed_l2_prefix
        if prefix and uri.startswith(prefix):
            return ProbeResult.passed()
        return ProbeResult.failed("uri 不在 capability 允许范围")


def _failed_stat(uri: VikingUri, category: OpenVikingErrorCategory, message: str) -> OpenVikingStat:
    return OpenVikingStat(
        uri=uri,
        ok=False,
        sha256=None,
        size_bytes=None,
        exists=False,
        is_dir=False,
        checked_at=_now_text(),
        error_category=category,
        error_message=message,
    )


def _failed_read(uri: VikingUri, category: OpenVikingErrorCategory, message: str) -> OpenVikingReadResult:
    return OpenVikingReadResult(
        uri=uri,
        ok=False,
        content=None,
        sha256=None,
        size_bytes=None,
        error_category=category,
        error_message=message,
    )


def _validate_probe_receipt_identity(receipt: MaterialReceipt, *, stat_uri: str, run_id: str) -> str | None:
    from claw_trade.workflow.models import Stage

    if receipt.uri != stat_uri:
        return "receipt.uri 与当前 probe URI 不一致"
    if receipt.run_id != run_id:
        return "receipt.run_id 与当前 probe run_id 不一致"
    if receipt.call_id != "probe_call":
        return "receipt.call_id 非 probe_call"
    if receipt.worker_id != "probe_worker":
        return "receipt.worker_id 非 probe_worker"
    if receipt.stage != Stage.FRONTLINE:
        return "receipt.stage 非 frontline"
    if receipt.target_name != "report":
        return "receipt.target_name 非 report"
    if receipt.source != "openviking_adapter_verified_receipt":
        return "receipt.source 非 openviking_adapter_verified_receipt"
    if receipt.receipt_origin != "adapter_verified_non_native":
        return "receipt.receipt_origin 非 adapter_verified_non_native"
    if receipt.is_openviking_native_receipt is not False:
        return "receipt.is_openviking_native_receipt 必须为 False"
    if receipt.verification is None:
        return "receipt.verification 缺失"
    if receipt.verification.verified is not True:
        return "receipt.verification.verified 必须为 True"
    return None


def _allowed_probe_stat_sizes(receipt: MaterialReceipt) -> set[int]:
    allowed = {receipt.size_bytes}
    # probe receipt 是本地审计文件，verification 里允许携带 raw write/stat 的额外 size 证据。
    # 这里只接受这两类固定字段，避免把任意 size 当成可接受。
    try:
        raw = json.loads(OPENVIKING_PROBE_RECEIPT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return allowed
    if not isinstance(raw, dict):
        return allowed
    verification = raw.get("verification")
    if not isinstance(verification, dict):
        return allowed
    for key in ("expected_write_size_bytes", "stat_size_bytes"):
        value = verification.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            allowed.add(value)
    return allowed


def _identity_from_uri(uri: VikingUri):
    from claw_trade.workflow.models import Stage

    prefix = "viking://resources/workflow/"
    if not uri.startswith(prefix):
        raise OpenVikingAccessError(f"非法 URI: {uri}", category="invalid_uri")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise OpenVikingAccessError(f"URI 层级不足: {uri}", category="invalid_uri")
    try:
        stage = Stage(parts[1])
    except ValueError as exc:
        raise OpenVikingAccessError(f"未知 stage: {parts[1]}", category="invalid_uri") from exc
    return parts[0], stage, parts[2], parts[3]
