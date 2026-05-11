# CN_A social 数据服务层实施覆盖矩阵（T-ACC-001）

更新日期：2026-05-07  
对应任务：`T-ACC-001`  
输入依据：`docs/CN_A_social数据服务层详细设计.md`、`docs/CN_A_social数据服务层开发任务清单.md`

状态说明：

- `已落地（待执行验证）`：实现/测试文件已存在，本轮仅建立覆盖映射，未在本任务中执行 pytest/live/fresh。
- `待执行`：路径或脚本已具备，但实际运行证据尚未生成。
- `风险待确认`：存在明确未闭环项，不能宣称已通过验收。
- `已记录（不阻断实施）`：测试口径或运行风险已按人工决议记录，不作为继续实施的阻断 gate；最终是否通过仍以后续集中测试结果为准。

关键真实约束（不得夸大）：

- `T-TST-002`：receipt hash mismatch 与单 endpoint timeout 的故障注入方式按人工决议记录，不作为继续实施的阻断 gate；真实集成运行仍待最后集中执行。
- `T-TST-003`：fresh 验收脚本已实现，但 fresh run 运行待执行，当前无真实 fresh run 产物。

## 机器校验数据

```json
{
  "schema_version": "cn_a_social_coverage_matrix.v1",
  "generated_on": "2026-05-07",
  "dld_source": "docs/CN_A_social数据服务层详细设计.md",
  "task_source": "docs/CN_A_social数据服务层开发任务清单.md",
  "dld_sections": [
    {"dld_section_id": "1", "hld_refs": ["HLD §1", "HLD §17", "HLD §19"], "task_ids": ["T-DOC-001", "T-ACC-001"], "test_ids": ["TEST-ACC-001"], "evidence_ids": ["EVID-DLD", "EVID-TASKLIST"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "2", "hld_refs": ["HLD §2", "HLD §10"], "task_ids": ["T-CFG-002", "T-PKG-001", "T-PRO-004"], "test_ids": ["TEST-UNIT-001", "TEST-SKILL-PACK-SCHEMA", "TEST-ACC-001"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "3", "hld_refs": ["HLD §5", "HLD §6", "HLD §13", "HLD §15"], "task_ids": ["T-POL-001", "T-POL-003", "T-DOC-001", "T-OPS-001"], "test_ids": ["TEST-CONTRACT-SKILL", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-SOCIAL-SKILL", "EVID-FRESH-SCRIPT"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "3.1", "hld_refs": ["HLD §5", "HLD §15"], "task_ids": ["T-POL-001", "T-PKG-004", "T-DOC-001"], "test_ids": ["TEST-CONTRACT-SKILL", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-FRESH-TEMPLATE", "EVID-DEPLOY-DOC"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "3.2", "hld_refs": ["HLD §7.2", "HLD §17 M2"], "task_ids": ["T-CFG-001", "T-OBS-001", "T-OPS-001", "T-DOC-001"], "test_ids": ["TEST-CONTRACT-CONFIG", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-DEPLOY-DOC"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "3.3", "hld_refs": ["HLD §3", "HLD §7.3"], "task_ids": ["T-POL-001", "T-POL-002", "T-GRD-002", "T-GRD-003"], "test_ids": ["TEST-CONTRACT-SKILL", "TEST-CONTRACT-GUARD"], "evidence_ids": ["EVID-SOCIAL-SKILL"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4", "hld_refs": ["HLD §1", "HLD §5", "HLD §10"], "task_ids": ["T-PKG-004", "T-ACC-001"], "test_ids": ["TEST-SKILL-ORCH", "TEST-ACC-001"], "evidence_ids": ["EVID-DLD", "EVID-COVERAGE-MATRIX"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.1", "hld_refs": ["HLD §6", "HLD §13"], "task_ids": ["T-POL-001", "T-POL-002", "T-POL-003", "T-GRD-002"], "test_ids": ["TEST-CONTRACT-SKILL", "TEST-SKILL-POLICY", "TEST-UNIT-001"], "evidence_ids": ["EVID-SOCIAL-SKILL"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.2", "hld_refs": ["HLD §2", "HLD §10"], "task_ids": ["T-PKG-001", "T-PKG-002", "T-PKG-003", "T-PKG-004"], "test_ids": ["TEST-SKILL-ORCH", "TEST-UNIT-001", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.3", "hld_refs": ["HLD §7.1", "HLD §7.2"], "task_ids": ["T-PRO-001", "T-PRO-002", "T-PRO-003", "T-PRO-004"], "test_ids": ["TEST-SKILL-PROVIDERS", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-INTEGRATION-TEST"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.4", "hld_refs": ["HLD §2.8", "HLD §8.2", "HLD §8.3"], "task_ids": ["T-CAC-001", "T-CAC-002", "T-CAC-003"], "test_ids": ["TEST-SKILL-CACHE-INSPECTION", "TEST-SKILL-CACHE-UPSERT", "TEST-UNIT-002"], "evidence_ids": ["EVID-SOCIAL-CACHE-CONFIG"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.5", "hld_refs": ["HLD §2.9", "HLD §8.4", "HLD §15"], "task_ids": ["T-EVD-001", "T-EVD-002", "T-EVD-003"], "test_ids": ["TEST-SKILL-EVIDENCE", "TEST-CONTRACT-GUARD", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-FRESH-SCRIPT", "EVID-INTEGRATION-TEST"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.6", "hld_refs": ["HLD §2.3", "HLD §4.4", "HLD §9"], "task_ids": ["T-MAT-001", "T-MAT-002", "T-MAT-003"], "test_ids": ["TEST-SKILL-MATCHING", "TEST-UNIT-002", "TEST-SKILL-ORCH"], "evidence_ids": ["EVID-ALIAS-RULES", "EVID-KEYWORD-CATEGORIES"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.7", "hld_refs": ["HLD §4.5", "HLD §11"], "task_ids": ["T-QLT-001"], "test_ids": ["TEST-SKILL-QUALITY", "TEST-UNIT-002", "TEST-CONTRACT-GUARD"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.8", "hld_refs": ["HLD §2.7", "HLD §12"], "task_ids": ["T-BRF-001"], "test_ids": ["TEST-SKILL-READER-BRIEF", "TEST-UNIT-002"], "evidence_ids": ["EVID-KEYWORD-CATEGORIES"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.9", "hld_refs": ["HLD §2.1", "HLD §14"], "task_ids": ["T-CFG-001", "T-CFG-002", "T-CFG-003", "T-MAT-001"], "test_ids": ["TEST-CONTRACT-CONFIG", "TEST-SKILL-PROFILE", "TEST-UNIT-001"], "evidence_ids": ["EVID-ALIAS-RULES", "EVID-KEYWORD-CATEGORIES"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "4.10", "hld_refs": ["HLD §13", "HLD §16", "HLD §18"], "task_ids": ["T-GRD-001", "T-GRD-002", "T-GRD-003", "T-TST-003"], "test_ids": ["TEST-CONTRACT-GUARD", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-FRESH-SCRIPT", "EVID-FRESH-TEMPLATE"], "status": "T-TST-003 脚本已实现，fresh run 运行待执行"},
    {"dld_section_id": "5", "hld_refs": ["HLD §5", "HLD §7.2"], "task_ids": ["T-PKG-003", "T-PKG-004", "T-TST-002"], "test_ids": ["TEST-SKILL-ORCH", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-INTEGRATION-TEST"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；运行待执行"},
    {"dld_section_id": "5.1", "hld_refs": ["HLD §5"], "task_ids": ["T-PKG-003", "T-PKG-004", "T-TST-002"], "test_ids": ["TEST-SKILL-ORCH", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-INTEGRATION-TEST"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；运行待执行"},
    {"dld_section_id": "5.2", "hld_refs": ["HLD §4.5", "HLD §11 failed"], "task_ids": ["T-PKG-003", "T-QLT-001", "T-TST-001"], "test_ids": ["TEST-SKILL-QUALITY", "TEST-UNIT-002"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "5.3", "hld_refs": ["HLD §2.8", "HLD §7.2"], "task_ids": ["T-CAC-003", "T-TST-002"], "test_ids": ["TEST-SKILL-CACHE-UPSERT", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-INTEGRATION-TEST"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；运行待执行"},
    {"dld_section_id": "6", "hld_refs": ["HLD §8.2", "HLD §8.3", "HLD §8.4"], "task_ids": ["T-CAC-001", "T-EVD-001", "T-EVD-003"], "test_ids": ["TEST-SKILL-CACHE-SCHEMA", "TEST-SKILL-EVIDENCE", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-SOCIAL-CACHE-CONFIG"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "6.1", "hld_refs": ["HLD §8.2"], "task_ids": ["T-CAC-001", "T-EVD-001"], "test_ids": ["TEST-SKILL-CACHE-SCHEMA"], "evidence_ids": ["EVID-SOCIAL-CACHE-CONFIG"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "6.2", "hld_refs": ["HLD §2.4", "HLD §10"], "task_ids": ["T-PKG-003", "T-PKG-004", "T-CAC-003", "T-EVD-003"], "test_ids": ["TEST-SKILL-ORCH", "TEST-SKILL-EVIDENCE", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "6.3", "hld_refs": ["HLD §17 M1", "HLD §17 M3"], "task_ids": ["T-POL-001", "T-PKG-004", "T-CAC-001", "T-EVD-003"], "test_ids": ["TEST-CONTRACT-SKILL", "TEST-SKILL-ORCH"], "evidence_ids": ["EVID-SOCIAL-SKILL"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "7", "hld_refs": ["HLD §7.1", "HLD §17 M2"], "task_ids": ["T-CFG-001", "T-OPS-001", "T-DOC-001"], "test_ids": ["TEST-CONTRACT-CONFIG", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-DEPLOY-DOC"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "7.1", "hld_refs": ["HLD §7.1", "HLD §7.3"], "task_ids": ["T-OPS-001", "T-OBS-001", "T-DOC-001"], "test_ids": ["TEST-CONTRACT-CONFIG", "TEST-INTEGRATION-001"], "evidence_ids": ["EVID-DEPLOY-DOC"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "8", "hld_refs": ["HLD §16", "HLD §17 M5"], "task_ids": ["T-TST-001", "T-TST-002", "T-TST-003"], "test_ids": ["TEST-UNIT-001", "TEST-UNIT-002", "TEST-INTEGRATION-001", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-INTEGRATION-TEST", "EVID-FRESH-TEMPLATE", "EVID-FRESH-SCRIPT"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；T-TST-003 运行待执行"},
    {"dld_section_id": "8.1", "hld_refs": ["HLD §16"], "task_ids": ["T-TST-001", "T-GRD-002", "T-GRD-003"], "test_ids": ["TEST-UNIT-001", "TEST-UNIT-002", "TEST-CONTRACT-GUARD"], "evidence_ids": ["EVID-SOCIAL-PACK-FIXTURE"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "8.2", "hld_refs": ["HLD §16", "HLD §17 M2"], "task_ids": ["T-TST-002", "T-TST-003", "T-DOC-001"], "test_ids": ["TEST-INTEGRATION-001", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-DEPLOY-DOC", "EVID-FRESH-TEMPLATE"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；T-TST-003 运行待执行"},
    {"dld_section_id": "8.3", "hld_refs": ["HLD §16", "HLD §17 M5"], "task_ids": ["T-TST-001", "T-TST-002", "T-TST-003"], "test_ids": ["TEST-UNIT-001", "TEST-INTEGRATION-001", "TEST-FRESH-SCRIPT"], "evidence_ids": ["EVID-FRESH-TEMPLATE", "EVID-FRESH-SCRIPT"], "status": "T-TST-002 故障注入口径已记录，不阻断实施；T-TST-003 运行待执行"},
    {"dld_section_id": "9", "hld_refs": ["HLD §1-§19"], "task_ids": ["T-ACC-001"], "test_ids": ["TEST-ACC-001"], "evidence_ids": ["EVID-COVERAGE-MATRIX"], "status": "已落地（待执行验证）"},
    {"dld_section_id": "10", "hld_refs": ["HLD §19"], "task_ids": ["T-DOC-001", "T-ACC-001"], "test_ids": ["TEST-ACC-001"], "evidence_ids": ["EVID-DLD", "EVID-DEPLOY-DOC"], "status": "已落地（待执行验证）"}
  ],
  "test_catalog": [
    {"test_id": "TEST-CONTRACT-SKILL", "path": "tests/contracts/test_cn_a_social_skill_manifest_contract.py"},
    {"test_id": "TEST-CONTRACT-CONFIG", "path": "tests/contracts/test_cn_a_social_config_contract.py"},
    {"test_id": "TEST-CONTRACT-GUARD", "path": "tests/contracts/test_cn_a_social_guard_contract.py"},
    {"test_id": "TEST-UNIT-001", "path": "tests/unit/test_cn_a_social_ticker_and_orchestrator.py"},
    {"test_id": "TEST-UNIT-002", "path": "tests/unit/test_cn_a_social_cache_quality_brief.py"},
    {"test_id": "TEST-INTEGRATION-001", "path": "tests/integration/test_cn_a_social_pack_integration.py"},
    {"test_id": "TEST-FRESH-SCRIPT", "path": "scripts/run_cn_a_social_fresh_acceptance.py"},
    {"test_id": "TEST-ACC-001", "path": "tests/contracts/test_cn_a_social_coverage_matrix_contract.py"},
    {"test_id": "TEST-SKILL-POLICY", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_policy.py"},
    {"test_id": "TEST-SKILL-ORCH", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_orchestrator.py"},
    {"test_id": "TEST-SKILL-PROVIDERS", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_providers.py"},
    {"test_id": "TEST-SKILL-CACHE-INSPECTION", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_cache_inspection.py"},
    {"test_id": "TEST-SKILL-CACHE-SCHEMA", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_cache_schema.py"},
    {"test_id": "TEST-SKILL-CACHE-UPSERT", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_cache_upsert.py"},
    {"test_id": "TEST-SKILL-EVIDENCE", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_evidence.py"},
    {"test_id": "TEST-SKILL-MATCHING", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_orchestrator.py"},
    {"test_id": "TEST-SKILL-QUALITY", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_quality.py"},
    {"test_id": "TEST-SKILL-READER-BRIEF", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_reader_brief.py"},
    {"test_id": "TEST-SKILL-PROFILE", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_profile.py"},
    {"test_id": "TEST-SKILL-PACK-SCHEMA", "path": "agents/social_analyst/skills/cn-a-social-data/tests/test_pack_schema.py"}
  ],
  "evidence_catalog": [
    {"evidence_id": "EVID-DLD", "path": "docs/CN_A_social数据服务层详细设计.md"},
    {"evidence_id": "EVID-TASKLIST", "path": "docs/CN_A_social数据服务层开发任务清单.md"},
    {"evidence_id": "EVID-DEPLOY-DOC", "path": "docs/CN_A_social数据服务层部署配置与容量评审.md"},
    {"evidence_id": "EVID-FRESH-TEMPLATE", "path": "docs/evidence/cn_a_social/fresh_acceptance_template.md"},
    {"evidence_id": "EVID-FRESH-SCRIPT", "path": "scripts/run_cn_a_social_fresh_acceptance.py"},
    {"evidence_id": "EVID-SOCIAL-SKILL", "path": "agents/social_analyst/skills/cn-a-social-data/SKILL.md"},
    {"evidence_id": "EVID-ALIAS-RULES", "path": "agents/social_analyst/skills/cn-a-social-data/config/alias_rules.yaml"},
    {"evidence_id": "EVID-KEYWORD-CATEGORIES", "path": "agents/social_analyst/skills/cn-a-social-data/config/keyword_categories.yaml"},
    {"evidence_id": "EVID-SOCIAL-PACK-FIXTURE", "path": "agents/social_analyst/skills/cn-a-social-data/tests/fixtures/cn_a_social_pack_v1_contract.json"},
    {"evidence_id": "EVID-INTEGRATION-TEST", "path": "tests/integration/test_cn_a_social_pack_integration.py"},
    {"evidence_id": "EVID-SOCIAL-CACHE-CONFIG", "path": "agents/social_analyst/skills/cn-a-social-data/scripts/config.py"},
    {"evidence_id": "EVID-COVERAGE-MATRIX", "path": "docs/CN_A_social数据服务层实施覆盖矩阵.md"}
  ],
  "open_issues": [
    {"issue_id": "OQ-001", "source": "DLD §10", "item": "OpenClaw/OpenViking/MongoDB 生产端口、容器编排、认证方式、数据库名", "status": "待确认", "owner": "平台运维负责人待指定"},
    {"issue_id": "OQ-002", "source": "DLD §10", "item": "QPS、日调用量、并发 run 数生产基线", "status": "待确认", "owner": "SRE/容量负责人待指定"},
    {"issue_id": "OQ-003", "source": "DLD §10", "item": "OpenViking L2 writer 运行时绑定", "status": "设计已定，运行验收待完成", "owner": "Runtime 负责人待指定"},
    {"issue_id": "OQ-004", "source": "DLD §10", "item": "P1 雪球热度 endpoint 与字段映射", "status": "第一阶段禁用", "owner": "数据源负责人待指定"},
    {"issue_id": "OQ-005", "source": "DLD §10", "item": "approved news summary 自然语言 schema", "status": "待确认", "owner": "news+social 数据契约负责人待指定"},
    {"issue_id": "OQ-006", "source": "DLD §10", "item": "MongoDB 生产 URI/database/collection 权限与 secret 注入", "status": "待确认", "owner": "DBA + 安全负责人待指定"},
    {"issue_id": "OQ-007", "source": "DLD §10", "item": "第一阶段是否强制 MongoDB", "status": "已有方向（M1 可不强制，M2 起强制）", "owner": "架构负责人待指定"},
    {"issue_id": "OQ-008", "source": "DLD §10", "item": "social 情绪评分职责边界", "status": "待确认", "owner": "Prompt/Guard 负责人待指定"},
    {"issue_id": "OQ-TST-002", "source": "T-TST-002", "item": "OpenViking receipt hash mismatch 与单 endpoint timeout 的故障注入方式", "status": "已记录（按人工决议不作为继续实施阻断 gate；真实集成运行待最后集中执行）", "owner": "测试负责人待指定"},
    {"issue_id": "OQ-TST-003", "source": "T-TST-003", "item": "600519 CN_A fresh run", "status": "脚本已实现/运行待执行（未产出真实 fresh run 证据目录）", "owner": "验收执行人待指定"}
  ]
}
```
