from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from claw_trade.artifacts.refs import (
    ApprovedMaterial,
    L1Claim,
    L2Entry,
    L2Index,
    MaterialReadRef,
    MaterialReceipt,
    OpenVikingReadCapability,
    VikingUri,
    validate_viking_uri_shape,
)
from claw_trade.workflow.models import Stage, WorkerCall
from claw_trade.workflow.workers import frontline_workers_for_market

_STAGE_WORKERS: dict[Stage, tuple[str, ...]] = {
    Stage.FRONTLINE: (
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
    ),
    Stage.INVESTMENT_DEBATE: ("bull_researcher", "bear_researcher"),
    Stage.INVESTMENT_DECISION: ("research_manager",),
    Stage.TRADE_DECISION: ("trader",),
    Stage.RISK_DEBATE: ("risk_challenger", "risk_guardian", "risk_moderator"),
    Stage.PORTFOLIO_DECISION: ("portfolio_manager",),
    Stage.FINAL_REPORT: ("report_polisher",),
}

_DEFAULT_MARKET = "US"


def _stage_workers(stage: Stage, market: str | None = None) -> tuple[str, ...]:
    if stage == Stage.FRONTLINE:
        return frontline_workers_for_market((market or _DEFAULT_MARKET).upper())
    return _STAGE_WORKERS[stage]

_UPSTREAM_STAGE: dict[Stage, Stage | None] = {
    Stage.FRONTLINE: None,
    Stage.INVESTMENT_DEBATE: Stage.FRONTLINE,
    Stage.INVESTMENT_DECISION: Stage.INVESTMENT_DEBATE,
    Stage.TRADE_DECISION: Stage.INVESTMENT_DECISION,
    Stage.RISK_DEBATE: Stage.TRADE_DECISION,
    Stage.PORTFOLIO_DECISION: Stage.RISK_DEBATE,
    Stage.FINAL_REPORT: Stage.PORTFOLIO_DECISION,
}

_ALLOWED_HARD_GATE_CATEGORIES: frozenset[str] = frozenset(
    {
        "hard_gate",
        "runtime_guards",
        "artifact_flow",
        "provider_request",
        "visible_tools",
        "tool_calls",
        "openviking_runtime_reads",
        "openviking_receipt",
        "l1_l2",
        "claims",
        "export_truthfulness",
        "combined_hard_gate",
        "ok",
    }
)


class ArtifactFlowError(ValueError):
    pass


def make_material_id(call: WorkerCall, receipt: MaterialReceipt) -> str:
    call_run_id = call.run_id
    call_id = call.call_id
    call_worker = call.worker_id
    call_stage = call.stage.value
    payload = "|".join(
        (
            call_run_id,
            call_id,
            call_worker,
            call_stage,
            receipt.target_name,
            receipt.uri,
            receipt.sha256,
        )
    )
    return f"mat-{sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Stage):
        return value.value
    return value


def manifest_entry_sha256(material: ApprovedMaterial) -> str:
    payload = _jsonable(asdict(material))
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _l2_prefix(material: ApprovedMaterial) -> VikingUri | None:
    if material.l2_index.index_uri:
        index_uri = material.l2_index.index_uri
        if index_uri.endswith("/index.json"):
            return f"{index_uri[: -len('/index.json')]}/"
    entries = material.l2_index.entries
    if not entries:
        return None
    head = entries[0].uri
    cutoff = head.rfind("/")
    if cutoff == -1:
        return None
    return f"{head[:cutoff]}/"


def _capability_id(material: ApprovedMaterial) -> str:
    material_sha = manifest_entry_sha256(material)
    return f"cap-{sha256(f'{material.material_id}|{material_sha}'.encode('utf-8')).hexdigest()[:24]}"


def _material_ref(material: ApprovedMaterial) -> MaterialReadRef:
    return MaterialReadRef(
        material_id=material.material_id,
        capability_id=_capability_id(material),
        worker_id=material.worker_id,
        stage=material.stage,
        l1_uri=material.l1_uri,
        l1_sha256=material.l1_sha256,
        l2_index_uri=material.l2_index_uri,
        l2_allowed_prefix=_l2_prefix(material),
        call_id=material.call_id,
        turn_index=material.turn_index,
        round_index=material.round_index,
        role_turn_index=material.role_turn_index,
    )


def _material_order_key(material: ApprovedMaterial) -> tuple[int, int, int, str]:
    stage_order = {
        Stage.FRONTLINE: 0,
        Stage.INVESTMENT_DEBATE: 1,
        Stage.INVESTMENT_DECISION: 2,
        Stage.TRADE_DECISION: 3,
        Stage.RISK_DEBATE: 4,
        Stage.PORTFOLIO_DECISION: 5,
        Stage.FINAL_REPORT: 6,
    }
    worker_order = {worker_id: index for index, worker_id in enumerate(_stage_workers(material.stage, market="CN_A"))}
    return (
        stage_order.get(material.stage, 99),
        material.turn_index,
        worker_order.get(material.worker_id, 99),
        material.call_id,
    )


def _missing_workers(
    required_workers: tuple[str, ...],
    selected: tuple[ApprovedMaterial, ...],
    stage: Stage,
) -> list[str]:
    present = {material.worker_id for material in selected}
    return [f"{worker_id}@{stage.value}" for worker_id in required_workers if worker_id not in present]


class ApprovedManifest:
    @classmethod
    def empty(cls) -> ApprovedManifest:
        return cls()

    def __init__(self) -> None:
        self._by_run: dict[str, list[ApprovedMaterial]] = {}
        self._by_id: dict[str, ApprovedMaterial] = {}
        self._worker_index: dict[tuple[str, Stage, str, int], str] = {}
        self._call_index: dict[tuple[str, str], str] = {}

    def add(self, material: ApprovedMaterial) -> ApprovedManifest:
        _validate_material_for_manifest(material)
        # 这里是 manifest 入库硬边界：内存入口和持久化入口都必须验证 hard gate 已通过。
        _require_hard_gate_pass(material)
        if material.material_id in self._by_id:
            raise ArtifactFlowError(f"重复 material_id: {material.material_id}")
        worker_key = (material.run_id, material.stage, material.worker_id, material.turn_index)
        if worker_key in self._worker_index:
            raise ArtifactFlowError(
                f"重复 worker turn: run_id={material.run_id} stage={material.stage.value} "
                f"worker_id={material.worker_id} turn_index={material.turn_index}"
            )
        call_key = (material.run_id, material.call_id)
        if call_key in self._call_index:
            raise ArtifactFlowError(f"重复 call: run_id={material.run_id} call_id={material.call_id}")
        self._by_id[material.material_id] = material
        self._by_run.setdefault(material.run_id, []).append(material)
        self._worker_index[worker_key] = material.material_id
        self._call_index[call_key] = material.material_id
        return self

    def all_for_run(self, run_id: str) -> tuple[ApprovedMaterial, ...]:
        return tuple(self._by_run.get(run_id, ()))

    def lookup(self, material_id: str) -> ApprovedMaterial | None:
        return self._by_id.get(material_id)

    def materials_for_stage(self, stage: Stage, run_id: str | None = None) -> tuple[ApprovedMaterial, ...]:
        return tuple(
            sorted(
                (material for material in self._materials(run_id) if material.stage == stage),
                key=_material_order_key,
            )
        )

    def stage_turn_count(self, stage: Stage, run_id: str | None = None) -> int:
        return len(self.materials_for_stage(stage=stage, run_id=run_id))

    def required_workers_for(self, stage: Stage, market: str | None = None) -> tuple[str, ...]:
        if stage not in _UPSTREAM_STAGE:
            raise ArtifactFlowError(f"unknown stage: {stage.value}")
        upstream = _UPSTREAM_STAGE[stage]
        if upstream is None:
            return ()
        return _stage_workers(upstream, market=market)

    def required_sources_for_worker_call(
        self, stage: Stage, worker_id: str, market: str | None = None
    ) -> tuple[tuple[str, Stage], ...]:
        if stage not in _STAGE_WORKERS:
            raise ArtifactFlowError(f"unknown stage: {stage.value}")
        stage_workers = _stage_workers(stage, market=market)
        if worker_id not in stage_workers:
            raise ArtifactFlowError(f"worker 不属于阶段: stage={stage.value} worker={worker_id}")
        if stage == Stage.FRONTLINE:
            return ()
        frontline_workers = _stage_workers(Stage.FRONTLINE, market=market)
        frontline_sources = tuple((item, Stage.FRONTLINE) for item in frontline_workers)
        if stage == Stage.INVESTMENT_DEBATE:
            if worker_id == "bear_researcher":
                return frontline_sources + (("bull_researcher", Stage.INVESTMENT_DEBATE),)
            return frontline_sources
        if stage == Stage.INVESTMENT_DECISION:
            return frontline_sources + tuple((item, Stage.INVESTMENT_DEBATE) for item in _stage_workers(Stage.INVESTMENT_DEBATE))
        if stage == Stage.TRADE_DECISION:
            return (("research_manager", Stage.INVESTMENT_DECISION),)
        if stage == Stage.RISK_DEBATE:
            risk_sources: tuple[tuple[str, Stage], ...] = ()
            if worker_id == "risk_guardian":
                risk_sources = (("risk_challenger", Stage.RISK_DEBATE),)
            elif worker_id == "risk_moderator":
                risk_sources = (
                    ("risk_challenger", Stage.RISK_DEBATE),
                    ("risk_guardian", Stage.RISK_DEBATE),
                )
            return frontline_sources + (("trader", Stage.TRADE_DECISION),) + risk_sources
        if stage == Stage.PORTFOLIO_DECISION:
            return (
                ("research_manager", Stage.INVESTMENT_DECISION),
                ("trader", Stage.TRADE_DECISION),
                *tuple((item, Stage.RISK_DEBATE) for item in _stage_workers(Stage.RISK_DEBATE)),
            )
        if stage == Stage.FINAL_REPORT:
            return (
                *frontline_sources,
                *tuple((item, Stage.INVESTMENT_DEBATE) for item in _stage_workers(Stage.INVESTMENT_DEBATE)),
                ("research_manager", Stage.INVESTMENT_DECISION),
                ("trader", Stage.TRADE_DECISION),
                *tuple((item, Stage.RISK_DEBATE) for item in _stage_workers(Stage.RISK_DEBATE)),
                ("portfolio_manager", Stage.PORTFOLIO_DECISION),
            )
        upstream = _UPSTREAM_STAGE[stage]
        if upstream is None:
            return ()
        return tuple((item, upstream) for item in _stage_workers(upstream, market=market))

    def has_worker(self, worker_id: str, stage: Stage, run_id: str | None = None) -> bool:
        for material in self._materials(run_id):
            if material.worker_id == worker_id and material.stage == stage:
                return True
        return False

    def has_worker_turn(self, worker_id: str, stage: Stage, turn_index: int, run_id: str | None = None) -> bool:
        for material in self._materials(run_id):
            if material.worker_id == worker_id and material.stage == stage and material.turn_index == turn_index:
                return True
        return False

    def for_downstream_stage(
        self, stage: Stage, run_id: str | None = None, market: str | None = None
    ) -> tuple[MaterialReadRef, ...]:
        required = self.required_workers_for(stage, market=market)
        if not required:
            return ()
        upstream_stage = _UPSTREAM_STAGE[stage]
        selected = self._selected_materials_for_workers(
            workers=required,
            upstream_stage=upstream_stage,
            run_id=run_id,
        )
        present_workers = {material.worker_id for material in selected}
        missing = [worker_id for worker_id in required if worker_id not in present_workers]
        if missing:
            raise ArtifactFlowError(
                f"下游材料缺失: run_id={run_id or '<auto>'} stage={stage.value} missing_workers={','.join(missing)}"
            )
        return tuple(_material_ref(material) for material in selected)

    def for_worker_call(
        self,
        stage: Stage,
        worker_id: str,
        run_id: str | None = None,
        turn_index: int = 0,
        market: str | None = None,
    ) -> tuple[MaterialReadRef, ...]:
        selected, missing = self._materials_for_worker_call(
            stage=stage,
            worker_id=worker_id,
            run_id=run_id,
            turn_index=turn_index,
            market=market,
        )
        if missing:
            raise ArtifactFlowError(
                f"worker 输入材料缺失: run_id={run_id or '<auto>'} "
                f"stage={stage.value} worker={worker_id} missing_sources={','.join(missing)}"
            )
        return tuple(_material_ref(material) for material in selected)

    def capabilities_for_downstream_stage(
        self,
        stage: Stage,
        run_id: str | None = None,
        market: str | None = None,
    ) -> tuple[OpenVikingReadCapability, ...]:
        refs = self.for_downstream_stage(stage=stage, run_id=run_id, market=market)
        out: list[OpenVikingReadCapability] = []
        for ref in refs:
            material = self._by_id[ref.material_id]
            out.append(
                OpenVikingReadCapability(
                    capability_id=ref.capability_id,
                    material_id=ref.material_id,
                    allowed_l1_uri=ref.l1_uri,
                    allowed_l1_sha256=ref.l1_sha256,
                    allowed_l2_prefix=ref.l2_allowed_prefix,
                    manifest_entry_sha256=manifest_entry_sha256(material),
                    allowed_l2_index_sha256=material.l2_index.index_sha256,
                )
            )
        return tuple(out)

    def capabilities_for_worker_call(
        self,
        stage: Stage,
        worker_id: str,
        run_id: str | None = None,
        turn_index: int = 0,
        market: str | None = None,
    ) -> tuple[OpenVikingReadCapability, ...]:
        refs = self.for_worker_call(
            stage=stage,
            worker_id=worker_id,
            run_id=run_id,
            turn_index=turn_index,
            market=market,
        )
        out: list[OpenVikingReadCapability] = []
        for ref in refs:
            material = self._by_id[ref.material_id]
            out.append(
                OpenVikingReadCapability(
                    capability_id=ref.capability_id,
                    material_id=ref.material_id,
                    allowed_l1_uri=ref.l1_uri,
                    allowed_l1_sha256=ref.l1_sha256,
                    allowed_l2_prefix=ref.l2_allowed_prefix,
                    manifest_entry_sha256=manifest_entry_sha256(material),
                    allowed_l2_index_sha256=material.l2_index.index_sha256,
                )
            )
        return tuple(out)

    def _materials_for_worker_call(
        self,
        stage: Stage,
        worker_id: str,
        run_id: str | None,
        turn_index: int,
        market: str | None,
    ) -> tuple[tuple[ApprovedMaterial, ...], list[str]]:
        if stage not in _STAGE_WORKERS:
            raise ArtifactFlowError(f"unknown stage: {stage.value}")
        stage_workers = _stage_workers(stage, market=market)
        if worker_id not in stage_workers:
            raise ArtifactFlowError(f"worker 不属于阶段: stage={stage.value} worker={worker_id}")
        if stage == Stage.FRONTLINE:
            return (), []

        frontline_workers = _stage_workers(Stage.FRONTLINE, market=market)
        frontline = self._selected_materials_for_workers(
            workers=frontline_workers,
            upstream_stage=Stage.FRONTLINE,
            run_id=run_id,
        )
        missing = _missing_workers(frontline_workers, frontline, Stage.FRONTLINE)

        if stage == Stage.INVESTMENT_DEBATE:
            debate = tuple(
                material
                for material in self.materials_for_stage(Stage.INVESTMENT_DEBATE, run_id=run_id)
                if material.turn_index < turn_index
            )
            if len(debate) < turn_index:
                missing.append(f"investment_debate_prior_turns<{turn_index}")
            return frontline + debate, missing

        if stage == Stage.INVESTMENT_DECISION:
            debate = self.materials_for_stage(Stage.INVESTMENT_DEBATE, run_id=run_id)
            present_debate_workers = {material.worker_id for material in debate}
            for required_worker in _stage_workers(Stage.INVESTMENT_DEBATE):
                if required_worker not in present_debate_workers:
                    missing.append(f"{required_worker}@{Stage.INVESTMENT_DEBATE.value}")
            return frontline + debate, missing

        if stage == Stage.TRADE_DECISION:
            manager = self._selected_materials_for_workers(
                workers=("research_manager",),
                upstream_stage=Stage.INVESTMENT_DECISION,
                run_id=run_id,
            )
            return manager, _missing_workers(("research_manager",), manager, Stage.INVESTMENT_DECISION)

        if stage == Stage.RISK_DEBATE:
            trader = self._selected_materials_for_workers(
                workers=("trader",),
                upstream_stage=Stage.TRADE_DECISION,
                run_id=run_id,
            )
            risk = tuple(
                material
                for material in self.materials_for_stage(Stage.RISK_DEBATE, run_id=run_id)
                if material.turn_index < turn_index
            )
            missing.extend(_missing_workers(("trader",), trader, Stage.TRADE_DECISION))
            if len(risk) < turn_index:
                missing.append(f"risk_debate_prior_turns<{turn_index}")
            return frontline + trader + risk, missing

        if stage == Stage.PORTFOLIO_DECISION:
            manager = self._selected_materials_for_workers(
                workers=("research_manager",),
                upstream_stage=Stage.INVESTMENT_DECISION,
                run_id=run_id,
            )
            trader = self._selected_materials_for_workers(
                workers=("trader",),
                upstream_stage=Stage.TRADE_DECISION,
                run_id=run_id,
            )
            risk = self.materials_for_stage(Stage.RISK_DEBATE, run_id=run_id)
            missing = _missing_workers(("research_manager",), manager, Stage.INVESTMENT_DECISION)
            missing.extend(_missing_workers(("trader",), trader, Stage.TRADE_DECISION))
            present_risk_workers = {material.worker_id for material in risk}
            for required_worker in _stage_workers(Stage.RISK_DEBATE):
                if required_worker not in present_risk_workers:
                    missing.append(f"{required_worker}@{Stage.RISK_DEBATE.value}")
            return manager + trader + risk, missing

        if stage == Stage.FINAL_REPORT:
            debate = self.materials_for_stage(Stage.INVESTMENT_DEBATE, run_id=run_id)
            manager = self._selected_materials_for_workers(
                workers=("research_manager",),
                upstream_stage=Stage.INVESTMENT_DECISION,
                run_id=run_id,
            )
            trader = self._selected_materials_for_workers(
                workers=("trader",),
                upstream_stage=Stage.TRADE_DECISION,
                run_id=run_id,
            )
            risk = self.materials_for_stage(Stage.RISK_DEBATE, run_id=run_id)
            portfolio = self._selected_materials_for_workers(
                workers=("portfolio_manager",),
                upstream_stage=Stage.PORTFOLIO_DECISION,
                run_id=run_id,
            )
            missing = _missing_workers(("research_manager",), manager, Stage.INVESTMENT_DECISION)
            missing.extend(_missing_workers(("trader",), trader, Stage.TRADE_DECISION))
            missing.extend(_missing_workers(("portfolio_manager",), portfolio, Stage.PORTFOLIO_DECISION))
            present_debate_workers = {material.worker_id for material in debate}
            for required_worker in _stage_workers(Stage.INVESTMENT_DEBATE):
                if required_worker not in present_debate_workers:
                    missing.append(f"{required_worker}@{Stage.INVESTMENT_DEBATE.value}")
            present_risk_workers = {material.worker_id for material in risk}
            for required_worker in _stage_workers(Stage.RISK_DEBATE):
                if required_worker not in present_risk_workers:
                    missing.append(f"{required_worker}@{Stage.RISK_DEBATE.value}")
            return frontline + debate + manager + trader + risk + portfolio, missing

        upstream = _UPSTREAM_STAGE[stage]
        if upstream is None:
            return (), []
        required = _stage_workers(upstream, market=market)
        selected = self._selected_materials_for_workers(workers=required, upstream_stage=upstream, run_id=run_id)
        return selected, _missing_workers(required, selected, upstream)

    def _materials(self, run_id: str | None) -> tuple[ApprovedMaterial, ...]:
        if run_id is not None:
            return tuple(self._by_run.get(run_id, ()))
        if not self._by_run:
            return ()
        if len(self._by_run) == 1:
            only_run_id = next(iter(self._by_run))
            return tuple(self._by_run.get(only_run_id, ()))
        raise ValueError("run_id is required when manifest contains multiple runs")

    def _selected_materials_for_workers(
        self,
        workers: tuple[str, ...],
        upstream_stage: Stage | None,
        run_id: str | None,
    ) -> tuple[ApprovedMaterial, ...]:
        materials = self._materials(run_id=run_id)
        selected: dict[str, ApprovedMaterial] = {}
        for material in materials:
            if material.worker_id not in workers:
                continue
            if upstream_stage is not None and material.stage != upstream_stage:
                continue
            if material.worker_id not in selected or material.turn_index >= selected[material.worker_id].turn_index:
                selected[material.worker_id] = material
        return tuple(selected[worker_id] for worker_id in workers if worker_id in selected)

    def _selected_materials_for_sources(
        self,
        sources: tuple[tuple[str, Stage], ...],
        run_id: str | None,
    ) -> tuple[ApprovedMaterial, ...]:
        materials = self._materials(run_id=run_id)
        wanted = set(sources)
        selected: dict[tuple[str, Stage], ApprovedMaterial] = {}
        for material in materials:
            key = (material.worker_id, material.stage)
            if key in wanted:
                selected[key] = material
        return tuple(selected[source] for source in sources if source in selected)


class ManifestStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._root_dir = root_dir or Path("runs")
        self._cache: dict[str, ApprovedManifest] = {}

    def load(self, run_id: str) -> ApprovedManifest:
        if run_id in self._cache:
            return self._cache[run_id]
        manifest = ApprovedManifest.empty()
        manifest_path = self._manifest_path(run_id)
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in payload.get("materials", ()):
                manifest.add(_material_from_payload(item))
        self._cache[run_id] = manifest
        return manifest

    def add(self, run_id: str, material: ApprovedMaterial) -> None:
        if material.run_id != run_id:
            raise ArtifactFlowError(
                f"run_id 不匹配: arg={run_id} material.run_id={material.run_id}"
            )
        manifest = self.load(run_id)
        manifest.add(material)
        self._persist(run_id, manifest)

    def _manifest_path(self, run_id: str) -> Path:
        return self._root_dir / run_id / "openviking" / "approved-manifest.json"

    def _persist(self, run_id: str, manifest: ApprovedManifest) -> None:
        manifest_path = self._manifest_path(run_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "materials": [_material_payload(material) for material in manifest.all_for_run(run_id)],
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )


def _validate_material_for_manifest(material: ApprovedMaterial) -> None:
    # 这里是 hard gate 边界：没有完整批准证据的材料不能写入 manifest。
    if not material.approved_at.strip():
        raise ArtifactFlowError(f"材料缺少 approved_at: {material.material_id}")
    if not str(material.hard_gate_result_path).strip():
        raise ArtifactFlowError(f"材料缺少 hard_gate_result_path: {material.material_id}")
    if material.l1_size_bytes <= 0:
        raise ArtifactFlowError(f"L1 大小非法: {material.material_id}")
    if not material.l1_sha256.strip():
        raise ArtifactFlowError(f"L1 sha256 不能为空: {material.material_id}")
    l1_uri_check = validate_viking_uri_shape(
        material.l1_uri,
        material.run_id,
        material.stage,
        material.worker_id,
        material.call_id,
    )
    if not l1_uri_check.ok:
        raise ArtifactFlowError(f"L1 URI 非法: {material.l1_uri} ({l1_uri_check.reason})")
    if material.l2_index_uri:
        index_uri_check = validate_viking_uri_shape(
            material.l2_index_uri,
            material.run_id,
            material.stage,
            material.worker_id,
            material.call_id,
        )
        if not index_uri_check.ok:
            raise ArtifactFlowError(f"L2 index URI 非法: {material.l2_index_uri} ({index_uri_check.reason})")
    for entry in material.l2_index.entries:
        entry_check = validate_viking_uri_shape(
            entry.uri,
            material.run_id,
            material.stage,
            material.worker_id,
            material.call_id,
        )
        if not entry_check.ok:
            raise ArtifactFlowError(f"L2 entry URI 非法: {entry.uri} ({entry_check.reason})")


def _require_hard_gate_pass(material: ApprovedMaterial) -> None:
    raw_path = material.hard_gate_result_path
    resolved = raw_path if raw_path.is_absolute() else (Path.cwd() / raw_path)
    # 这里是批准落库边界：必须能证明 hard gate 已通过，才允许写入 approved manifest。
    if not resolved.is_file():
        raise ArtifactFlowError(
            f"hard gate 结果文件不存在: material_id={material.material_id} path={resolved}"
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactFlowError(
            f"hard gate 结果文件读取失败: material_id={material.material_id} path={resolved} ({exc})"
        ) from exc
    except JSONDecodeError as exc:
        raise ArtifactFlowError(
            f"hard gate 结果文件不是合法 JSON: material_id={material.material_id} path={resolved} ({exc})"
        ) from exc
    if not _guard_payload_passed(payload):
        raise ArtifactFlowError(
            f"hard gate 未通过: material_id={material.material_id} path={resolved}"
        )


def _guard_payload_passed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    # 这里必须先校验 hard gate 类别：防止任意 JSON 伪装成通过结果写入 approved manifest。
    category_value = payload.get("category")
    if not isinstance(category_value, str):
        return False
    normalized_category = category_value.strip().lower()
    if normalized_category not in _ALLOWED_HARD_GATE_CATEGORIES:
        return False
    signals: list[bool] = []
    if "ok" in payload:
        ok_value = payload["ok"]
        if not isinstance(ok_value, bool):
            return False
        signals.append(ok_value)
    if "status" in payload:
        status_value = payload["status"]
        if not isinstance(status_value, str):
            return False
        normalized = status_value.strip().lower()
        if normalized in {"pass", "passed", "ok"}:
            signals.append(True)
        elif normalized in {"fail", "failed", "error", "blocked"}:
            signals.append(False)
        else:
            return False
    if not signals:
        return False
    return all(signals)


def _material_payload(material: ApprovedMaterial) -> dict[str, Any]:
    payload = _jsonable(asdict(material))
    return payload


def _material_from_payload(payload: dict[str, Any]) -> ApprovedMaterial:
    l2_index_payload = payload["l2_index"]
    entries = tuple(
        L2Entry(
            evidence_id=entry["evidence_id"],
            uri=entry["uri"],
            kind=entry["kind"],
            source=entry["source"],
            sha256=entry["sha256"],
            size_bytes=entry["size_bytes"],
        )
        for entry in l2_index_payload.get("entries", ())
    )
    l2_index = L2Index(
        entries=entries,
        empty_reason=l2_index_payload.get("empty_reason"),
        index_uri=l2_index_payload.get("index_uri"),
        index_sha256=l2_index_payload.get("index_sha256"),
        index_size_bytes=l2_index_payload.get("index_size_bytes"),
    )
    claims = tuple(
        L1Claim(
            claim_id=claim["claim_id"],
            kind=claim["kind"],
            text=claim["text"],
            value=claim.get("value"),
            required_evidence_kinds=tuple(claim.get("required_evidence_kinds", ())),
            evidence_ids=tuple(claim.get("evidence_ids", ())),
        )
        for claim in payload.get("l1_claims", ())
    )
    return ApprovedMaterial(
        material_id=payload["material_id"],
        run_id=payload["run_id"],
        call_id=payload["call_id"],
        worker_id=payload["worker_id"],
        stage=Stage(payload["stage"]),
        target_name=payload["target_name"],
        l1_uri=payload["l1_uri"],
        l1_sha256=payload["l1_sha256"],
        l1_size_bytes=payload["l1_size_bytes"],
        l2_index_uri=payload.get("l2_index_uri"),
        l2_index=l2_index,
        l1_claims=claims,
        approved_at=payload["approved_at"],
        hard_gate_result_path=Path(payload["hard_gate_result_path"]),
        turn_index=int(payload.get("turn_index", 0)),
        round_index=int(payload.get("round_index", 1)),
        role_turn_index=int(payload.get("role_turn_index", 1)),
    )
