# 微信确认卡未送达：目标机只读根因核查

核查日期：2026-09-23。事故时间均为 America/New_York（UTC-04:00）。
目标机：192.168.1.21。未更改部署代码、账号或配置，未重启服务，未发送测试消息。

## 结论

本次扫码走了普通登录路径，向账号索引追加新账号；插件只清理相同微信用户标识的旧账号，而两个账号文件中的用户标识不同，所以旧账号被保留。后台主动发送要求全局唯一账号，因双账号而拒绝发送。确认卡的待确认状态在发送之前就已建立，发送异常又被捕获后直接返回，因此网页有卡而微信没有确认内容。

这不是凭“旧账号存在”推测：已有新账号保存时间、普通登录调用日志、双账号启动日志、部署插件源码及构建产物的相同清理条件，以及部署 Python 字节码的离线复现相互支持。

## 已证实与边界

- 旧账号保存于 2026-08-15，新账号保存于 2026-09-22 20:42:47.889。
- 账号标识为 3c297f8f3d56-im-bot、6e3b9f603c3a-im-bot；微信用户标识确实不同，下列采集用 SHA256 表示，未保存 token。
- 网关于 20:42:31 完成 web.login.start，20:42:48 完成 web.login.wait，20:42:50 同时启动新旧账号。
- 20:39～20:50 UI 日志中有 save-channel-config-via-openclaw，没有 start-wechat-reconnect 的 POST；当前也不存在 .ui-wechat-reconnect.json。这与普通登录而非账号替换一致。
- 部署前端 index.html 引用 index--8Uplh5i.js。该产物中的“重新连接”处理在 state 不是 connected 且 replacementRequired 不是 true 时，直接调用启用配置接口；其余情况才调用 start-wechat-reconnect。部署后端启用配置可以转入普通 QR 登录。
- 未保留点击瞬间的浏览器状态和 get-channel-status 响应，故不能断言具体是哪次状态响应让前端选中普通登录分支，也不能声称用户具体点击了哪个按钮。
- 离线复现使用实际部署 .pyc、真实账号索引、受控状态及卡片输入，不调用真实网关、不发送消息、不写生产业务状态。它证明部署代码的故障机制，不冒充手机端到端测试。
- 20:48:19 微信入站处理完成；20:48:43 真实报告 worker 已开始运行，早于 20:48:52 会话中后一次确认及 20:49:05 网页确认 409。前轮只读会话快照记录该次后续错误为 gateway cli timeout after 8000ms。不能把 409 单独解释为重复确认，也不能用服务端成功证明手机显示。

## 部署字节码证据

实际从 /opt/claw-trade/current/app/python/claw_trade/ui_backend 导入：
- wechat_reconnect.pyc SHA256 62d74b0990e41e7d16a4cce5f64f171161e6beb41f10cd79de13b884f8c89815
- channel_bridge.pyc SHA256 206f836690dc115416efd02484799882b3099c1c2099cf8bab72a80f0b1d093b
- channel_text_inbound.pyc SHA256 652bfe96aae5b1f8f9ac31d1ef5a4e2c85db69259ffa767162b10cbeec4bd112

使用部署 runtime/python/bin/python，PYTHONDONTWRITEBYTECODE=1，PYTHONPATH 指向该 release 的 app/python 和 runtime/python-site-packages。反汇编确认：
- resolve_current_unique_account_id：len(registered) != 1 即返回 None（原始源码行 689～693）。
- _handle_confirmation_intent 内 _finish：先生成卡片回复、写入待确认状态，再发送；捕获 Exception 后 RETURN_CONST None（原始源码行 203～212）。

离线重放结果：
```json
{"mode": "OFFLINE deployed-pyc replay; actual stored account IDs; controlled inventory and chat input; no RPC, no send, no production state write", "uniqueAccount": null, "error": ["微信账号已变化，消息没有发出。"], "backgroundReturnedNormally": true, "pendingCount": 1, "ack": {"handled": true, "replyText": "收到，正在识别标的，请稍等。", "state": "confirmation_processing", "deferFinalReply": true}}
```

## 目标机只读采集输出

```text
EVIDENCE-BEGIN
release: /opt/claw-trade/releases/claw-trade-production-1.0.2-20260824T025054Z
accounts: [{"accountId": "3c297f8f3d56-im-bot", "savedAt": "2026-08-15T23:51:55.045Z", "userIdSha256": "4718dd87c34d4fa31fb32021c350d654f10493063bad94f1a0c1fd498d859041"}, {"accountId": "6e3b9f603c3a-im-bot", "savedAt": "2026-09-23T00:42:47.889Z", "userIdSha256": "9b21872549637ad789d52b698028f84197f7580fe006b355c951d5a0631d1ef7"}]
reconnect-state-exists: False
plugin: 2.4.4-clawtrade.2 {'extensions': ['./index.ts'], 'runtimeExtensions': ['./dist/index.js'], 'channel': {'id': 'openclaw-weixin', 'label': 'openclaw-weixin', 'selectionLabel': 'openclaw-weixin', 'docsPath': '/channels/openclaw-weixin', 'docsLabel': 'openclaw-weixin', 'blurb': 'Weixin channel', 'order': 75}, 'install': {'npmSpec': '@tencent-weixin/openclaw-weixin', 'defaultChoice': 'npm', 'minHostVersion': '>=2026.3.22'}}
FILE /opt/claw-trade/shared/runtime-assets/openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/src/auth/accounts.ts SHA256 57d5d7b992a8e6a00aea329ba187761c2c72479aacb578f0fb7d27fecda6f424
90: /** Add accountId to the persistent index (no-op if already present). */
91: export function registerWeixinAccountId(accountId: string): void {
92:   const existing = listIndexedWeixinAccountIds();
93:   if (existing.includes(accountId)) return;
94:   replaceIndexedWeixinAccountIds([...existing, accountId]);
95: }
FILE /opt/claw-trade/shared/runtime-assets/openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/src/auth/accounts.ts SHA256 57d5d7b992a8e6a00aea329ba187761c2c72479aacb578f0fb7d27fecda6f424
117: export function clearStaleAccountsForUserId(
118:   currentAccountId: string,
119:   userId: string,
120:   onClearContextTokens?: (accountId: string) => void,
121: ): void {
122:   if (!userId) return;
123:   const allIds = listIndexedWeixinAccountIds();
124:   for (const id of allIds) {
125:     if (id === currentAccountId) continue;
126:     const data = loadWeixinAccount(id);
127:     if (data?.userId?.trim() === userId) {
128:       logger.info(`clearStaleAccountsForUserId: removing stale account=${id} (same userId=${userId})`);
129:       onClearContextTokens?.(id);
130:       clearWeixinAccount(id);
131:       unregisterWeixinAccountId(id);
132:     }
133:   }
FILE /opt/claw-trade/shared/runtime-assets/openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/src/channel.ts SHA256 95734ddc94ccbc8a0d7ea92e48f37f71ca4824c7d7217122054d829b067fa988
520:       if (result.connected && result.botToken && result.accountId) {
521:         try {
522:           const normalizedId = normalizeAccountId(result.accountId);
523:           saveWeixinAccount(normalizedId, {
524:             token: result.botToken,
525:             baseUrl: result.baseUrl,
526:             userId: result.userId,
527:           });
528:           registerWeixinAccountId(normalizedId);
529:           if (result.userId) {
530:             clearStaleAccountsForUserId(normalizedId, result.userId, clearContextTokensForAccount);
531:           }
532:           triggerWeixinChannelReload();
533:           logger.info(`loginWithQrWait: saved account data for accountId=${normalizedId}`);
534:         } catch (err) {
GATEWAY 649 2026-09-22T20:42:31.240-04:00 [ws] ⇄ res ✓ web.login.start 3641ms conn=182ec679…c351 id=8079aa7c…9483
GATEWAY 651 2026-09-22T20:42:48.128-04:00 [ws] ⇄ res ✓ web.login.wait 11868ms conn=264bd70a…aec0 id=39884eea…a483
GATEWAY 659 2026-09-22T20:42:50.817-04:00 [openclaw-weixin] [3c297f8f3d56-im-bot] starting weixin provider (https://ilinkai.weixin.qq.com)
GATEWAY 660 2026-09-22T20:42:50.819-04:00 [openclaw-weixin] [6e3b9f603c3a-im-bot] starting weixin provider (https://ilinkai.weixin.qq.com)
GATEWAY 818 2026-09-22T20:48:43.762-04:00 [agent/embedded] [trace:embedded-run] prep stages: runId=run-20260923-004819-0416cea3-frontline-t00-news_analyst-20260923T004819050377Z-671e1e7d sessionId=single-worker-run-20260923-004819-0416cea3-frontline-t00-news_analyst-20260923T004819050377Z-671e1e7d phase=stream-ready totalMs=11705 stages=workspace-sandbox:2844ms@2844ms,skills:0ms@2844ms,core-plugin-tools:755ms@3599ms,bootstrap-context:1867ms@5466ms,bundle-tools:1510ms@6976ms,system-prompt:2ms@6978ms,session-resource-loader:2129ms@9107ms,agent-session:2589ms@11696ms,stream-setup:9ms@11705ms
JOURNAL 2026-09-22T20:42:31-04:00 ubuntu claw-trade-ui[2677]: INFO:     192.168.1.38:5469 - "POST /api/ui/save-channel-config-via-openclaw HTTP/1.1" 200 OK
JOURNAL 2026-09-22T20:48:19-04:00 ubuntu claw-trade-ui[2677]: INFO:     127.0.0.1:41294 - "POST /api/ui/channel-inbound-message HTTP/1.1" 200 OK
JOURNAL 2026-09-22T20:49:05-04:00 ubuntu claw-trade-ui[2677]: INFO:     192.168.1.38:12095 - "POST /api/ui/confirm-intent-draft HTTP/1.1" 409 Conflict
EVIDENCE-END
```

## 插件构建产物核查

```text
dist/src/auth/accounts.js SHA256 c50fbf7f0d5715152bb76d9256da50c1d503691fa1a10e4281eb1d2317de212d
function registerWeixinAccountId(accountId) {
    const existing = listIndexedWeixinAccountIds();
    if (existing.includes(accountId))
        return;
    replaceIndexedWeixinAccountIds([...existing, accountId]);
}
/** Remove accountId from the persistent index. */
export function unregisterWeixinAccountId(accountId) {
    const existing = listIndexedWeixinAccountIds();
    const updated = existing.filter((id) => id !== accountId);
    if (updated.length !== existing.length) {
        replaceIndexedWeixinAccountIds(updated);
    }
}
export function replaceIndexedWeixinAccountIds(accountIds) {
    writeJsonAtomic(resolveAccountIndexPath(), [...new Set(accountIds)]);
}
/**
 * Remove stale accounts that share the same userId as the newly-bound account.
 * Called after a successful QR login to ensure only the latest account remains
 * for a given WeChat user, preventing ambiguous contextToken matches.
 *
 * @param onClearContextTokens callback to clear context tokens for the removed accou
function clearStaleAccountsForUserId(currentAccountId, userId, onClearContextTokens) {
    if (!userId)
        return;
    const allIds = listIndexedWeixinAccountIds();
    for (const id of allIds) {
        if (id === currentAccountId)
            continue;
        const data = loadWeixinAccount(id);
        if (data?.userId?.trim() === userId) {
            logger.info(`clearStaleAccountsForUserId: removing stale account=${id} (same userId=${userId})`);
            onClearContextTokens?.(id);
            clearWeixinAccount(id);
            unregisterWeixinAccountId(id);
        }
    }
}
function resolveAccountsDir() {
    return path.join(resolveWeixinStateDir(), "accounts");
}
function resolveAccountPath(accountId) {
    return path.join(resolveAccountsDir(), `${accountId}.json`);
}
export function listStoredWeixinAccountIds() {
    try {
        return fs.readdirSync(resolveAccountsDir(), { withFileTypes: true })
            .filter((entry) => entry.isFile() && entry.name.endsWit
dist/src/channel.js SHA256 03b087a3ab80ea1cc3a84861dce6930a0177f0e316f0889ae8ad94b583347146
loginWithQrWait: async (params) => {
            // sessionKey is forwarded by the client after loginWithQrStart (runtime param extension).
            const sessionKey = params.sessionKey || params.accountId || "";
            const savedBaseUrl = params.accountId
                ? loadWeixinAccount(params.accountId)?.baseUrl?.trim()
                : "";
            const result = await waitForWeixinLogin({
                sessionKey,
                apiBaseUrl: savedBaseUrl || DEFAULT_BASE_URL,
                timeoutMs: params.timeoutMs,
            });
            if (result.connected && result.botToken && result.accountId) {
                try {
                    const normalizedId = normalizeAccountId(result.accountId);
                    saveWeixinAccount(normalizedId, {
                        token: result.botToken,
                        baseUrl: result.baseUrl,
                        userId: result.userId,
                    });
                    registerWeixinAcco
registerWeixinAccountId(normalizedId);
                    if (waitResult.userId) {
                        clearStaleAccountsForUserId(normalizedId, waitResult.userId, clearContextTokensForAccount);
                    }
                    void triggerWeixinChannelReload();
                    log(`\n已将此 OpenClaw 连接到微信。`);
                }
                catch (err) {
                    logger.error(`auth.login: failed to save account data accountId=${waitResult.accountId} err=${String(err)}`);
                    log(`⚠️  保存账号数据失败: ${String(err)}`);
                }
            }
            else if (waitResult.alreadyConnected) {
                // Server confirmed this OpenClaw is already bound to the scanned bot;
                // local credentials are intact, nothing to persist. Exit successfully
                // so that automated installers don't treat re-runs as login failures.
                // The QR poller already wrote the user-facing message to stdout, so
        

exit
Connection to 192.168.1.21 closed.
```

## 修复方向（本轮未实施）

账号已有凭据时，扫码换账号必须由现有账号替换流程负责，避免普通登录追加不同用户账号而破坏单账号约定；确认卡发送失败要有明确投递状态和可恢复路径，不能直接吞掉异常。只删除旧账号可处理当前状态，但无法消除再次发生的入口缺陷。不能简单移除唯一账号检查或自动选择一个账号来掩盖问题。


## 1.0.3 部署后仍有两个账号：再次只读核查

用户追问“为什么还有两个微信号”后再次 SSH 核对：

- 当前 release 为 `claw-trade-production-1.0.3-20260923T144533Z`。
- 账号索引依然只有原先两项 `3c297f8f3d56-im-bot`、`6e3b9f603c3a-im-bot`；索引 mtime 为 `2026-09-23T00:42:47.939798Z`（本机 9/22 20:42），没有在本次升级时重写。
- 索引 SHA256：`172e20f3fdaa51002036c3ed30fe1b58a56607544f84caf062a86f5564d9621a`。
- 两个账号文件的 savedAt、mtime、userId SHA256 与前述事故核查相同：旧号 8/15，新号 9/22；不存在升级后再次扫码新增账号的文件证据。
- 运行插件 `dist/src/auth/accounts.js` SHA256 仍为 `c50fbf7f0d5715152bb76d9256da50c1d503691fa1a10e4281eb1d2317de212d`；现场再次读取，仍是追加索引、仅清理同 userId 的规则。
- 9/23 10:52:52.414 与 .427 的新网关启动日志分别启动上述两个账号，说明重启重新加载了既有索引。
- 自新 UI 进程 10:52:47 启动至本次核查，journal 中 `start/poll/cancel/recover-wechat-reconnect` 及 `save-channel-config-via-openclaw` 的 POST 均为 0；`.ui-wechat-reconnect.json` 仍不存在。
- `web/state.py:920-924` 启动时调用换号恢复；`wechat_reconnect.py:286-290` 在没有换号状态时直接返回 None，不检查/清理已有账号。
- 目标机导入新版真实 `.pyc`，以临时缺失换号状态和禁止任何网关调用的对象执行 `recover()`：结果 None、网关/账号操作 0。只在临时目录创建锁文件，不触及生产账号或业务状态。

分层根因：最初普通扫码的“追加账号、只替换同一微信用户”规则与产品“只能有一个账号”的规则不一致，留下双账号；1.0.3 修改后续扫码入口，但没有历史账号迁移，部署也没有清理任何现有账号。因而新版启动原样加载历史双账号。修复并未完成现有数据恢复，不能把代码已部署等同于当前微信已恢复可用。
