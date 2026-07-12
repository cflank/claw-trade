# OpenClaw 外部看门狗设计

## 目标

当 OpenClaw 进程仍存活但 `18789` 持续无响应时，由进程外 systemd 看门狗有限次重启 control。看门狗不得中断在途报告，不得接管 rescue/UI/kiosk，不得与更新或恢复出厂并发。

## 已批准恢复策略

- 同一 control `MainPID` 的 OpenViking `1933/health` 与 OpenClaw `18789/health` 连续三次失败后，允许第一次重启。
- 第一次重启后仍不健康，距第一次重启满 5 分钟才允许第二次重启。
- 第二次重启后仍不健康，距第二次重启满 15 分钟才允许第三次重启。
- 第三次重启后仍不健康，进入 `exhausted`，继续探活但不再自动重启，等待人工处理。
- 两个健康端点恢复后，自动清除重试状态；人工修复无需执行额外重置命令。
- 重试状态放在 systemd root-only `RuntimeDirectory`。机器重启视为人工介入，状态随 `/run` 清除，并重新受 5 分钟启动宽限保护。

## 健康与资格

- OpenViking 必须返回 HTTP 2xx JSON，且 `healthy=true`、`status=ok`。
- OpenClaw 必须返回 HTTP 2xx JSON，且 `ok=true`、`status=live`。
- 固定 localhost URL，禁用代理继承，每次请求有 3 秒超时和响应大小上限。
- control 非 active、`MainPID` 为零或变化、启动不足 5 分钟、rescue active、更新 service active、语义锁存在时，本轮退出。
- 健康状态优先于冷却状态：端点恢复即清除重试次数。

## 两类内核锁

完整安装创建两个固定文件：

```text
/opt/claw-trade/host-operations.lock
/opt/claw-trade/report-active.lock
owner=root group=clawtrade mode=0660
parent=/opt/claw-trade owner=root 且 group/other 不可写
```

文件只提供 `flock` 能力，不授予 `clawtrade` systemctl 或 root 文件写权限。禁止 symlink、非普通文件、错误 owner/group/mode，也禁止回退到 `/run/lock`。

### 宿主操作锁

锁顺序为：宿主 flock -> `maintenance.lock`/`apply.lock` -> 删除、release 切换或 systemctl 动作。

- `ProductionMaintenanceLock.hold()` 非阻塞取得独占宿主锁，再创建 `maintenance.lock`，覆盖更新下载和恢复出厂全过程；失败沿用“系统正在维护”错误。
- root apply helper 阻塞取得独占宿主锁，保留等待中的 apply 请求，覆盖安装、切换、重启、健康检查和回滚。
- watchdog 三次探测后非阻塞取得独占宿主锁；失败则本轮正常退出。取得后重新检查所有资格，再执行一次 control restart。

### 报告执行锁

- 报告队列必须先取得共享报告锁，再发布 `RUNNING`；完成 artifact 提交并写入成功、失败或取消终态后，才释放共享锁。
- 取消请求仅写入 `CANCELLED` 不代表执行结束；只有实际 workflow 后台线程退出、确认不再可能写 artifact 后，队列才可发布取消终态并释放锁。线程仍存活时保持锁和运行态，不启动下一任务。
- 共享锁获取失败时任务保持非 `RUNNING` 并明确失败，禁止无锁执行或 fallback。
- watchdog 在宿主锁内非阻塞取得独占报告锁；失败表示至少一个报告仍在执行，本轮不得重启。
- UI 崩溃或被强杀时，内核自动释放文件描述符，不使用可能永久残留的 sentinel，也不解析 workflow `state.json` 猜测活跃状态。
- watchdog 不取消报告、不改写 run 状态、不处理 artifact。

## 重试状态

状态文件位于 `/run/claw-trade-watchdog/retry.json`，目录由 watchdog service 以 `RuntimeDirectory=claw-trade-watchdog`、`RuntimeDirectoryMode=0700`、`RuntimeDirectoryPreserve=yes` 创建，使状态跨 oneshot timer 轮次保留、机器重启后清除。状态只包含本次 boot 的尝试次数、下一次允许重启的 monotonic deadline 和 `exhausted` 标志。

状态文件必须是 root-owned `0600` 普通文件，禁止 symlink。读取到非法 JSON、非法字段、错误 owner/mode，或临时文件写入、`fsync`、原子替换失败时，helper 非零退出且不得 restart。缺失状态只表示本机重启后或健康清零后的首次尝试。

看门狗只有在同时持有宿主独占锁和报告独占锁、并完成最终资格复核后，才在同目录写 root-only 临时文件、flush + `fsync`、原子替换状态，再调用 restart。因此 restart 超时或 helper 被杀也不会绕过次数上限：

```text
attempt 1 -> next allowed = now + 5 minutes
attempt 2 -> next allowed = now + 15 minutes
attempt 3 -> exhausted
```

冷却或 exhausted 期间 timer 继续每分钟探活。健康则清除状态；不健康则只记录，不执行 restart。

## systemd 与所有权

- root oneshot service + 60 秒 timer；开机 5 分钟后首次运行。
- helper 固定安装到 `/usr/local/lib/claw-trade/claw-trade-watchdog`，不读取或 source `runtime.env`。
- control 使用 `KillMode=control-group`、`TimeoutStopSec=90`。
- watchdog 只执行 `systemctl restart claw-trade-control.service`，不启动或停止 UI、kiosk、rescue。
- UI 的 `Requires=control`、现有 Restart/OnFailure、rescue 与 UI 的 Conflicts 继续由 systemd 所有。目标机已证明正常 control restart 后原 UI 保持 active；若 systemd 或 rescue 将 UI 停止，看门狗刻意不拉起它，等待 rescue 或人工处理，优先保证不反向停止 rescue。
- 删除 control 恢复后的长轮询和前端恢复代码。watchdog 单次执行只包含三次探测、最终复核、一次 restart，`TimeoutStartSec` 必须由全部 HTTP、systemctl 查询、动作和 sleep 上限计算并留余量。
- apply service timeout 必须覆盖最长 watchdog 执行上限加原更新事务预算。

## 发布生命周期

- 首个包含修正版看门狗的版本走完整生产安装；安装锁文件、helper、units，daemon-reload，但目标机在验收通过前保持 timer disabled。
- 后续在线升级验证并保留既有锁 inode，不截断或替换被持有的锁文件；新版 apply helper维护 helper/units。
- 包内 preflight 只检查 helper、units、安装脚本包含锁文件合同；不能把尚未安装的宿主 inode 当成包内输入。
- 完整安装完成后执行 host verification，检查 `/opt/claw-trade` 父目录及两个实际锁 inode 的路径、owner/group/mode、普通文件和非 symlink 合同。
- 后续升级必须验证并复用既有锁 inode；缺失或不安全时明确失败，禁止静默创建替代锁或继续运行。
- 卸载先停 timer 和运行中的 watchdog service，再删除 helper、units 和锁文件；锁仍被持有时不得删除。

## 验证

1. 聚焦测试覆盖健康、启动宽限、PID 变化、三次探测、5/15 分钟冷却、第三次 exhausted、健康后清零。
2. 跨组件测试覆盖 maintenance/apply/watchdog 宿主锁竞争和请求保留。
3. 报告锁测试覆盖成功、失败、取消、异常和进程被杀后的自动释放。
4. watchdog 已持有报告独占锁时，任务不能发布 `RUNNING`；任务持共享锁时 watchdog 不能 restart。
5. 非法、不安全或无法原子写入的 retry state 失败关闭；跨三轮 oneshot 保留，机器重启后清除。
6. rescue 在任意时间出现时，证明 watchdog 从不调用 UI/kiosk/rescue 动作。
7. 最坏执行预算测试包含全部 HTTP、systemctl、sleep 和 restart 上限。
8. `systemd-analyze verify` 在等价生产布局通过。
9. 目标机先保持 timer disabled，验证真实健康 JSON、锁权限和正常轮次。
10. 真实在途 `/report` 期间暂停 OpenClaw，证明 watchdog 不重启且 run/artifact 不变；报告终态后再次注入，证明恢复生效。
11. 持续故障跨越 5 分钟和 15 分钟窗口，证明最多三次重启，第三次后只探活；人工修复后自动清零。

## 非目标

- 不修复 OpenClaw 自死锁源码。
- 不自动取消、恢复或重跑被中断报告。
- 不让 watchdog 操作 UI、kiosk 或 rescue。
- 不新增 supervisor、第二套 recovery service 或业务状态解析器。
