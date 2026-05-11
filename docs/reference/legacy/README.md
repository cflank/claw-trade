# 旧文档恢复索引

恢复时间：2026-05-11。

恢复来源：git reflog 删除前快照 `7cb296e`。该快照与 `b383c77`、`842fec7` 在 `docs/` 下的非 evidence 旧文档内容一致；后续当前 `HEAD` 将这些顶层旧文档替换为四份主文档。

## 定位

本目录保留删除前旧文档全文。当前主入口仍是：

- [需求设计](../../需求设计.md)
- [架构设计](../../架构设计.md)
- [详细设计](../../详细设计.md)
- [任务清单](../../任务清单.md)

四份主文档是入口、结论归并和当前执行口径，不是旧文档全文替代。需要追溯 provider 矩阵、字段合同、测试项、容量评审或历史 prompt 细节时，必须读本目录对应旧源文。

若旧文档与当前主文档、`AGENTS.md` 或已批准实现冲突，以当前主文档、`AGENTS.md` 和已验证实现为准；旧文档用于保留细节和追溯来源，不自动恢复旧决策。

## 完整性

- 已恢复非 evidence 旧文档：23 份。
- 恢复旧源文件合计：25,585 行，不含本 README。
- evidence 文件未移入本目录，仍保留在 `docs/evidence/`。

## Control 与通用参考

- [control迁移方案.md](control迁移方案.md)
- [control详细设计方案.md](control详细设计方案.md)
- [control详细设计实施任务清单.md](control详细设计实施任务清单.md)
- [current_workflow_inventory.md](current_workflow_inventory.md)
- [原版prompt.md](原版prompt.md)
- [claw对齐CN改造参考.md](claw对齐CN改造参考.md)
- [frontline工具资料包改造说明.md](frontline工具资料包改造说明.md)
- [stock_news_agent_design.md](stock_news_agent_design.md)
- [dev_mongodb_tushare_setup.md](dev_mongodb_tushare_setup.md)

## CN_A Frontline Provider 矩阵

- [CN_A_frontline_provider矩阵资料层落地方案.md](CN_A_frontline_provider矩阵资料层落地方案.md)
- [CN_A_frontline_provider矩阵资料层详细设计.md](CN_A_frontline_provider矩阵资料层详细设计.md)
- [CN_A_frontline_provider矩阵资料层开发任务清单.md](CN_A_frontline_provider矩阵资料层开发任务清单.md)

## CN_A Fundamental 数据服务层

- [CN_A_fundamental数据服务层总体设计.md](CN_A_fundamental数据服务层总体设计.md)
- [CN_A_fundamental数据服务层详细设计.md](CN_A_fundamental数据服务层详细设计.md)
- [CN_A_fundamental数据服务层开发任务清单.md](CN_A_fundamental数据服务层开发任务清单.md)

## CN_A News 数据服务层

- [CN_A_news数据服务层设计方案.md](CN_A_news数据服务层设计方案.md)
- [CN_A_news数据服务层详细设计.md](CN_A_news数据服务层详细设计.md)
- [CN_A_news数据服务层开发任务清单.md](CN_A_news数据服务层开发任务清单.md)

## CN_A Social 数据服务层

- [CN_A_social数据服务层设计方案.md](CN_A_social数据服务层设计方案.md)
- [CN_A_social数据服务层详细设计.md](CN_A_social数据服务层详细设计.md)
- [CN_A_social数据服务层开发任务清单.md](CN_A_social数据服务层开发任务清单.md)
- [CN_A_social数据服务层实施覆盖矩阵.md](CN_A_social数据服务层实施覆盖矩阵.md)
- [CN_A_social数据服务层部署配置与容量评审.md](CN_A_social数据服务层部署配置与容量评审.md)
