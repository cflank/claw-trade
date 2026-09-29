# 1.0.3 无加密生产部署

日期：2026-09-23。用户明确要求提供生产版本、不加密、部署目标机。

## 发布内容

- 目标：`192.168.1.21`，网页 `http://192.168.1.21:5175/`。
- 新版：`claw-trade-production-1.0.3-20260923T144533Z`。
- 回滚版本：`claw-trade-production-1.0.2-20260824T025054Z`，完整保留。
- 版本声明：`pyproject.toml`、`uv.lock` 更新至 1.0.3；运行版本由生产 release 名称确定。
- 只发布本次微信账号替换入口、确认卡投递反馈与恢复修复，详见 `wechat-confirmation-fix-20260923.md`。
- 保留既有生产包装：普通 Python 3.12 字节码和前端构建文件，没有执行 Virbox 加密流程。原有授权功能和依赖不变。
- 隔离发布源码：`.runtime/wechat-release-1.0.3/src`，由 HEAD `f7b690a7c7ca401fa8c0177fb9fbb6b6a6b93160` 加本次微信修复组成。
- 没有带入工作区已有的定时选股 PDF、费用、Chrome 脚本或 OpenClaw 脏改动。原工作区内容保留。

## 构建与校验

1. 独立审查确认隔离源码导入路径，运行 bridge、inbound、wechat_reconnect、channel_inbound_bridge 四组测试：**152 passed**。
2. 前端设置页此前最终 **36 passed**，最终生产构建通过。
3. 对旧部署两份 `.pyc` 与 HEAD 源码编译结果规范化比较：字节码、常量、变量名、参数和异常表相同；忽略文件路径、行号并规范化集合顺序。证明生产基线一致。
4. 按原生产构建方式 `optimize=2` 编译两份 Python 模块，连同前端 `dist` 和校验清单生成增量包。
5. 增量包本地和目标机 SHA256 一致：
   `9fee217f46891160d2774fca1210c953f769de99316cd8d2f96b762bd6d2d62d`。
6. 在目标机普通复制旧 release 到新目录（`cp -a`，没有硬链接），覆盖已构建增量；移除复制来的解释器缓存以及旧前端资产。
7. 新目录根权限 0755、root:root；以 clawtrade 身份在生产工作目录执行 `bin/claw-trade-preflight` 通过。首轮从 `/root` 执行因 clawtrade 无权返回工作目录失败，改用实际服务工作目录后通过；未修改检查标准。
8. 包目录 audit 首轮发现旧 release 运行产生的 `__pycache__`；按既有 builder 清理新目录缓存后，检查 **186146 entries，通过**。不修改旧 release 或共享业务数据。
9. 清单中 5 个文件哈希逐一通过，替换文件没有硬链接。

## 部署与运行验证

- 先检查生产 systemd 运行环境、runtime.env 和 5175/1933/18789 健康，三项均 200。
- 切换前报告无运行任务、无排队任务；取得现有 host/report/data-work 三把宿主锁，避免切换时打断或启动新任务。
- 复用既有发布辅助函数原子切换 `current`，重启 control 和 UI；失败路径会切回旧版并重启。实际没有触发回滚。
- 部署检查输出：`DEPLOYED_AND_HEALTHY 1.0.3`。
- control/UI 均 active/running；UI 5175、OpenViking 1933、OpenClaw 18789 均 HTTP 200。
- 维护接口 `currentVersion=1.0.3`；运行 UI 进程环境的 release root 指向新版。
- 授权接口 `status=activated`；`systemctl --failed` 为 0。
- 从开发机访问真实网页 JS，与本地构建 SHA256 相同：
  `d6ae261ec48dec7012c8699177817e12464e8f1c50375c750e0312ae41ae0fb3`。
- 目标机导入 `current/app/python/.../channel_bridge.pyc` 和 `channel_text_inbound.pyc`，用隔离的单/双账号状态和既有测试辅助复测：单账号检查通过；双账号仍拒发；`failure_visible=True`、`card_recoverable=True`。未发送微信、未发起模型任务、未写生产业务状态。该检查证明真实部署字节码行为，不代表手机端到端验收。

## 当前微信状态与验收边界

真实 `/api/ui/get-channel-status?probe=true` 返回：

- `state=disconnected`。
- `replacementRequired=true`。
- 提示：检测到多个微信账号，请重新连接并只保留当前账号。
- `canSendText=false`、`canSendFile=false`。

此次部署未删除任一现有账号。用户需在设置中重新连接并扫码，完成已有双账号状态的收敛。真实 Chrome 和手机收发尚未验证；不能宣称微信已恢复正常发送。

已有 guard 合同检查引用失效文档路径的失败仍保留，详见本次修复证据；未为通过测试修改无关文档。

## 发布文件

- 本地增量包：`dist/production/claw-trade-production-1.0.3-20260923T144533Z.delta.tar.gz`，配套 `.sha256`。
- 完整生产包：目标机 `/opt/claw-trade/packages/claw-trade-production-1.0.3-20260923T144533Z.tar.gz`（约 2.0 GiB），同目录有 `.sha256`。
- 完整包通过项目原有归档路径/类型校验及内容审计：`OK: audited 186144 entries`，随后输出 `FULL_PACKAGE_VERIFIED`。两项检查均成功退出。
- 完整包 SHA256：`02e9ac4101fe1cb1aec590a77d1a163946b3ca744029e3f2f718ec80e0326ce8`。
- 完整包保存在目标机；本地保留增量包、隔离源码和证据。曾启动可选的完整包本地复制，因传输速度约 1.8 MB/s 而取消，未完成的本地文件已删除，不作为交付文件。目标机完整包不受影响。
- 完整包没有发布到远程自动更新渠道；本次为用户授权的直接部署。
