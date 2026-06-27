# 远程 Ubuntu 部署流程

日期：2026-06-27
状态：从 `memory/` 与现有工控机部署文档整理

## 结论

远程 Ubuntu 部署应走“本地构建生产包 -> 传到干净 Ubuntu 机器 -> 安装到 `/opt/claw-trade` -> systemd/kiosk 启动 -> 运行验收”的路径。

不要把开发仓库、`.git`、`tests/`、`docs/`、`memory/`、`.env.local`、签名私钥或客户 API KEY 放到客户机器。不要在客户机器上用临时脚本热补已发布包；包内容必须在构建阶段固定，安装阶段只做校验、解包和切换 `current`。

## Memory 摘要

- `memory/2026-05-05.md`：固定本地运行时口径是 `scripts/start-control-runtime.sh`，OpenViking `1933`，OpenClaw gateway `18789`，不走 invest sidecar。
- `memory/2026-05-05.md`：运行时启动失败必须保留真实原因，不能用 mock、stub、fake 或 fallback 冒充 live 通过。
- `memory/2026-06-09.md`：`.runtime/dev-services` 会被启动脚本清理，固定 seed 应放在 `.runtime/factory-seeds` 或生产包数据目录，运行期增量写入单独 overlay。
- `memory/2026-06-18.md`：live 验收需要明确 runtime profile、preflight、OpenViking health、OpenClaw gateway health 和真实请求证据。
- `docs/项目文档/工控机生产部署设计.md`：生产形态是 `/opt/claw-trade/releases/<version>` 和 `/opt/claw-trade/current`，运行状态写入 `/opt/claw-trade/shared`。
- `docs/项目文档/工控机第一版部署验证门与最小生产包实施计划.md`：远程 VM 验收步骤包括 VM 准备、`scp` 生产包、SSH 安装、Virbox runtime/SDK 安装、写运行配置、preflight、systemd 启动和 `/report` 边界验收。

## 本地构建前

1. 确认构建机有 Python 3.12、Node.js、pnpm、tar 和项目虚拟环境。
2. 构建前完成前端 dist、OpenClaw dist、agent 资产和 seed 数据准备。
3. 生产包必须通过内容审计，禁止：
   - `.git`
   - `tests/`
   - `docs/`
   - `memory/`
   - `web/research-ui/src/`
   - `.env.local`
   - 密钥、token、cookie、session、签名私钥
4. 构建输出放在 `dist/production/` 或 `.runtime/production-build/`；这些目录是本地构建产物，不提交 git。

## 远程 Ubuntu 准备

目标机器是 Ubuntu x86_64，最小前置条件：

```bash
sudo apt-get update
sudo apt-get install -y python3.12 curl tar chromium-browser
sudo groupadd --system clawtrade || true
sudo useradd --system --no-create-home --gid clawtrade --shell /usr/sbin/nologin clawtrade || true
sudo groupadd --system clawkiosk || true
sudo useradd --system --no-create-home --gid clawkiosk --shell /usr/sbin/nologin clawkiosk || true
```

如果是 Virbox 授权版本，先把官方 Linux x86_64 runtime/SDK 放到机器安全目录，例如 `/opt/virbox/linux-x86_64`，并用官方非交互安装方式安装。授权码、控制台密码和客户 API KEY 不写入仓库和安装包。

## 传包

从构建机执行：

```bash
REMOTE=deploy@<ubuntu-ip>
ARCHIVE="$(ls -1t dist/production/claw-trade-production-*.tar.gz | head -n 1)"
test -f "${ARCHIVE}"
scp "${ARCHIVE}" "${ARCHIVE}.sha256" "${REMOTE}:/tmp/"
```

如还没有正式安装脚本，按当前设计在远程机手动执行安装步骤；正式安装脚本应只做 archive 校验、解包、权限设置和 `current` 原子切换。

## 安装

在远程 Ubuntu 上执行：

```bash
ARCHIVE="$(ls -1t /tmp/claw-trade-production-*.tar.gz | head -n 1)"
RELEASE="$(basename "${ARCHIVE}" .tar.gz)"

sudo mkdir -p /opt/claw-trade/releases
sudo mkdir -p /opt/claw-trade/shared/{cache,config,data,license,logs,openclaw,queues,reports,sessions,tmp,updates}
sudo tar --no-same-owner --no-same-permissions -xzf "${ARCHIVE}" -C /opt/claw-trade/releases
sudo ln -sfn "/opt/claw-trade/releases/${RELEASE}" /opt/claw-trade/current.next
sudo mv -Tf /opt/claw-trade/current.next /opt/claw-trade/current
sudo chown -R clawtrade:clawtrade /opt/claw-trade/releases/"${RELEASE}" /opt/claw-trade/shared
sudo chown -h clawtrade:clawtrade /opt/claw-trade/current
```

生产包应自带：

```text
/opt/claw-trade/current/bin/claw-trade-control
/opt/claw-trade/current/bin/claw-trade-ui
/opt/claw-trade/current/bin/claw-trade-preflight
/opt/claw-trade/current/systemd/*.service
/opt/claw-trade/current/web/dist/
```

## 运行配置

在远程 Ubuntu 写入运行期配置：

```bash
sudo install -d -m 0750 -o clawtrade -g clawtrade /opt/claw-trade/shared/config
sudo tee /opt/claw-trade/shared/config/runtime.env >/dev/null <<'EOF'
CLAW_TRADE_PRODUCTION=1
CLAW_TRADE_UI_HOST=127.0.0.1
CLAW_TRADE_UI_PORT=5175
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
EOF
sudo chown clawtrade:clawtrade /opt/claw-trade/shared/config/runtime.env
sudo chmod 0640 /opt/claw-trade/shared/config/runtime.env
```

如果启用 Virbox：

```bash
sudo tee -a /opt/claw-trade/shared/config/runtime.env >/dev/null <<'EOF'
CLAW_TRADE_LICENSE_REQUIRED=1
CLAW_TRADE_VIRBOX_STATUS_COMMAND=/opt/claw-trade/shared/license/virbox_probe
EOF
```

## Preflight

在远程 Ubuntu 上执行：

```bash
sudo -u clawtrade /opt/claw-trade/current/bin/claw-trade-preflight
```

必须确认：

- `/opt/claw-trade/current` 不指向开发仓库；
- `web/dist` 存在；
- OpenClaw runtime 资产存在；
- shared 目录可写；
- 配置文件不含明文测试密钥；
- 生产模式不读取 `.runtime/dev-services/runtime.env` 或 `.env.local`。

## systemd 启动

安装 unit 并启动：

```bash
sudo install -m 0644 /opt/claw-trade/current/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claw-trade-control.service claw-trade-ui.service
sudo systemctl enable --now claw-trade-kiosk.service
```

检查：

```bash
systemctl status claw-trade-control.service --no-pager
systemctl status claw-trade-ui.service --no-pager
curl -fsS http://127.0.0.1:5175/
curl -fsS http://127.0.0.1:18789/health
```

如果服务失败，先看 journald 和 `/opt/claw-trade/shared/logs`，不要用临时 patch 绕过失败。

## 验收

最低验收：

1. 机器上不存在 `/home/frank/src/claw-trade` 生产依赖。
2. `/opt/claw-trade/current` 指向当前 release。
3. UI 能从本机打开。
4. OpenViking 和 OpenClaw gateway health 通过。
5. `/report` 从 UI 或 API 触发后仍走 OpenClaw worker wake，不走 direct LLM 或 Python materializer。
6. 授权无效时，报告生成和数据刷新被后端阻断；前端只展示状态，不拥有授权判断。
7. 日志、诊断包和导出包不含 API KEY、token、cookie、session 或授权码明文。

## 清理规则

可以删除：

- `.runtime/production-package/`
- `.runtime/production-build/`
- 过期的本地 tar 构建目录
- 旧的远程 `/tmp/claw-trade-production-*.tar.gz`

不要删除，除非已确认不再需要：

- `.runtime/factory-seeds/`：本地固定 seed 恢复目录；
- `.worktrees/`：里面可能有未提交实现；
- `/opt/claw-trade/shared/`：客户运行期配置、报告、日志、授权状态和数据；
- 已签名 release 包和对应 manifest/sha256/sig。
