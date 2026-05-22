# UI Blocker Register（P5-02）

更新时间：2026-05-19  
基线提交：`a4b31e1`

说明：本表只记录 blocker 事实、保守实现和停止条件，不把未探测能力写成成功。证据列来自源文档或已记录命令输出；运行时 probe 未完成的项只登记为待探测。

| 编号 | 状态 | owner | 影响 | 证据 | 保守行为 | 停止条件 |
|---|---|---|---|---|---|---|
| B-01 微信 ClawBot 映射 | 已查清 | UI 后端 | 需要把用户概念映射到 OpenClaw Channel | `docs/UI详细设计.md:19` 明确插件和 Channel ID；`:22-23` 记录“官方流程已确认、clean 运行态验收未完成”的当前状态；`:99` 给保守设计；`docs/UI实施任务清单.md:690` 锁定 B-01；`memory/2026-05-19.md:57-58` 记录命令证据摘要 | UI 文案只显示 `wechat_clawbot`，内部映射 `openclaw-weixin`，不向普通 UI 返回真实 channel id | 发现实现使用 `wechat`/`clawbot`/WeCom 或暴露内部映射时停止 |
| B-02 PDF 文件发送运行时能力 | 待探测 | 运行时集成 | 影响“发送完整报告”是否可用 | `docs/UI详细设计.md:132-134`、`docs/UI实施任务清单.md:691` 记录能力探测未跑通 | 发送前必须做真实 status/capabilities 检查；不可用时返回 `FILE_SEND_UNSUPPORTED` 或 `NOTIFICATION_UNAVAILABLE` | 出现“未探测先宣称可发送”或伪造 message id 时停止 |
| B-03 微信消息通知/回调接入 | 待探测 | 运行时集成 | 影响微信内动作是否能触发产品动作 | `docs/UI实施任务清单.md:692` 记录 fixed runtime 尚不会加载外部微信插件 | 仅消费 OpenClaw 入站回调；没有接入口时保持设备端入口，不自建微信协议 | 出现 claw-trade 直接实现微信协议或假回调成功时停止 |
| B-04 LLM 配置桥接 | 已查清 | UI 后端 | 影响设置页模型保存/测试入口 | `docs/UI详细设计.md:18` 列出 config/models 能力；`:20` 明确不需要 `llm.save/test`；`:100` 给桥接方案；`docs/UI实施任务清单.md:693` 锁定 B-04；`memory/2026-05-19.md:47` 记录决策摘要 | 通过 OpenClaw config/models 桥接，不自造 `llm.save/test` | 返回 OpenClaw 内部路径/hash/raw patch 时停止 |
| B-05 UI 后端形态 | 已决策 | UI 后端 | 影响 API 与 service 边界 | `docs/UI实施任务清单.md:66` 标记已关闭 | 保持传输无关 contract，前端只调用 claw-trade 产品 API | 引入旧 direct LLM/旧 workflow 或前端直连 OpenClaw 时停止 |
| B-06 前端框架 | 已决策 | 前端 | 影响三栏工程骨架 | `docs/UI实施任务清单.md:66` 标记已关闭 | 参考 `claw-invest` 兼容 React/Vite/TypeScript 形态 | 引入旧业务逻辑和内部诊断字段时停止 |
| B-07 数据源持久化/secret 存储 | 已决策 | 设置后端 | 影响 `.env.local` 边界和密钥安全 | `docs/UI实施任务清单.md:66` 标记已关闭 | 后端 allowlist 写入、replace-only、掩码回显；前端不读写文件 | API 返回真实密钥/路径或 controller 直接读 env 时停止 |
| B-08 完成摘要提取规则 | 已决策 | 报告后端 | 影响完成卡内容可信度 | `docs/UI实施任务清单.md:66` 标记已关闭 | 只从已保存报告、PM 结论和元数据提取；缺失就提示查看全文 | 新增简报 worker 或 LLM 补写摘要时停止 |
| B-09 图表状态来源 | 已查清 | 报告后端 | 影响图表证据展示 | `docs/UI实施任务清单.md:66` 标记已关闭 | 只用已保存证据聚合状态，不返回内部路径 | 用静态占位“图表正常”或返回内部路径时停止 |
| B-10 运行中取消规则 | 已决策 | 队列后端 | 影响任务状态一致性 | `docs/UI实施任务清单.md:66` 标记已关闭 | 仅 queued 可取消；running 返回 `TASK_NOT_CANCELLABLE` | 伪造 running->cancelled 成功时停止 |
