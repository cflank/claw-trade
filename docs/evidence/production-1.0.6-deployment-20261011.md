# 1.0.6 无加密生产部署

日期：2026-10-11（美东）。用户授权提交、推送并部署目标机。

## 根因

- 10-09 08:20 微信 bot 重新扫码登录，`clearWeixinAccountFiles` 清除了磁盘上的 `context-tokens.json`。
- 此后用户未给 bot 发消息，10-10、10-11 每日选股推送均为 `contextToken missing, sending without context`。插件只告警、照发，并只看 HTTP 状态就记为成功。iLink 不投递无 context 的消息（推断：用户发消息、token 恢复后，回复成功送达）。
- 10-10 另有 PDF 上传 CDN HTTP 500 三次失败，属于明确的发送失败。

## 修复（提交 141380a）

- openclaw-weixin `2.4.4-clawtrade.3`：文字及媒体发送缺 contextToken 时直接抛错；`sendMedia` 在 CDN 上传前检查。
- `sendmessage` 响应 `ret`/`errcode` 非 0 判失败。
- 构建：插件 16 个测试通过，typecheck/build 通过。产物 SHA256 `f7aa1a93859e44e1bfccdd8f009592a6866a5970658b33551239f09c7e67e36d`，本地与目标机一致。

## 部署

- 新版 `claw-trade-production-1.0.6-20261012T034927Z`；回滚 `current-backup` → 1.0.5。
- 部署前确认目标机插件 `dist/src/channel.js` 与 clawtrade.2 产物哈希一致。
- `cp -a` 1.0.5 后只替换 `runtime/assets/openclaw_plugins.tar` 中的 openclaw-weixin 包（依赖不变）；preflight 通过。
- 持有三把宿主锁原子切换 `current`，重启 control/UI，输出 `DEPLOYED_AND_HEALTHY 1.0.6`。UI、OpenClaw 网关、OpenViking 均返回 200；`systemctl --failed` 为 0。
- 运行中插件 `2.4.4-clawtrade.3`；日志 `restoreContextTokens: restored 1 tokens`。重启后约 30 秒内微信状态为 `connected`，`canSendText` 与 `canSendFile` 均为 true。

## 未验证

- 未发送测试消息；手机端收到推送和缺 token 时报错的真实表现，待下一次定时选股验证。
