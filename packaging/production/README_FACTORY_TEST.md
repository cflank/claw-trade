# claw-trade 厂家测试包

这个包用于厂家工控机第一轮联调，不是最终量产安装器。

当前包完成生产路径、systemd、kiosk、最小 rescue 服务、systemd 失败后 rescue takeover 触发器、恢复出厂后端、维护锁、设置页入口、signed manifest 检查、signed archive 手动安装、systemd updater timer 自动检查、服务重启健康检查和失败回滚基线。运行中任务取消、诊断包和完整售后救援功能尚未实现。

## 当前边界

- Python 应用以 Python 3.12 `.pyc` 形式交付，不直接包含 `src/` 源码树。
- 包含前端 `web/dist`、agent 运行资产、OpenClaw dist、当前 Python 依赖 site-packages。
- 如果已生成 `data/current-seed-*.tar`，包内会包含 CN_A/CRYPTO 当前 active Parquet seed 和 metadata-only 数据血缘。
- 不包含 `.git`、`tests/`、`docs/`、`memory/`、`web/research-ui/src/`、`.env.local`、签名私钥。
- 生产安装已集成 Virbox 状态 SDK 和后端授权门。安装脚本绑定授权码并启用授权检查；授权未激活、过期、吊销或运行时不可用时，报告和数据刷新功能会被阻止。

## 安装

```bash
sudo scripts/production/install_production_package.sh /path/to/claw-trade-production-1.0.0-时间戳.tar.gz
```

安装脚本会创建 `clawtrade`、`clawkiosk`，检查 kiosk 浏览器路径，校验 archive basename 和包内唯一顶层目录一致，再切换 `/opt/claw-trade/current`。
`/opt/claw-trade/releases` 和 `current`/`rescue-current` 链接归 root 所有；`/opt/claw-trade/shared` 归 `clawtrade` 写入运行期状态，systemd 服务的工作目录也在 shared 下。
安装脚本同时安装 root-owned 更新应用 helper 到 `/usr/local/lib/claw-trade/claw-trade-apply-update`，用于复验 signed manifest/archive、解包 release、切换版本、重启服务和健康检查；helper 运行期间持有 `/opt/claw-trade/shared/updates/apply.lock`，应用 release 内不保存 root helper。

如果 `/tmp/update-signing-public.pem` 存在，安装脚本会自动安装到：

```text
/etc/claw-trade/update-signing-public.pem
```

不要手动 `tar -xzf` 后再用通配符 `ln -sfn` 切换 `current`；这会绕过安装脚本的包名和顶层目录校验。

安装脚本只接受可信本地生产包。当前代码可从对象存储检查 signed manifest，并手动下载、校验、安装 signed archive。安装脚本默认写入 `CLAW_TRADE_UPDATE_BASE_URL` 和 `CLAW_TRADE_AUTO_UPDATE_INSTALL=0`；全新 systemd 安装也会启用 `claw-trade-auto-update.timer`，但默认只检查，不自动下载安装。需要自动安装时显式设置 `CLAW_TRADE_AUTO_UPDATE_INSTALL=1`。

## 启动

```bash
sudo install -m 0644 /opt/claw-trade/current/systemd/*.service /etc/systemd/system/
sudo install -m 0644 /opt/claw-trade/current/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claw-trade-control.service claw-trade-ui.service claw-trade-kiosk.service claw-trade-auto-update.timer
```

浏览器打开：

```text
http://127.0.0.1:5175/
```

## 最低环境

- Ubuntu 24.04 x86_64
- systemd
- Python 3.12
- Node.js 22.12+
- Chromium 或 Google Chrome kiosk browser；可用 `CLAW_TRADE_KIOSK_BROWSER_BIN=/usr/bin/google-chrome` 覆盖默认值
- 图形会话，默认 `DISPLAY=:0`
- curl, tar
- 联网安装 Node.js、Chrome/Chromium 和可选 MongoDB runtime；离线工控机需要提前放入这些 runtime。

MongoDB runtime 如果包内没有预置，会由 runtime 脚本下载；离线工控机需要提前放入 runtime。
