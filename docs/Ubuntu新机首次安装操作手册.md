# Ubuntu 新机首次安装操作手册

默认从 R2 读取最新版本：

```bash
BASE="https://download.cflank-trade.top/delivery"
```

R2 必须有：

```text
delivery/latest.txt
delivery/install_from_r2_ubuntu.sh
delivery/install_factory_test_ubuntu.sh
delivery/validate_production_archive.py
delivery/update-signing-public.pem
delivery/senseshield-lcc-2.7.5.69040-amd64.deb
delivery/claw-trade-production-版本-时间.tar.gz
delivery/claw-trade-production-版本-时间.tar.gz.sha256
```

`latest.txt` 内容只写一行 release 名，不带 `.tar.gz`，例如：

```text
claw-trade-production-1.0.0-20260706T053438Z
```

## 1. 准备

准备好：

```text
Ubuntu 24.04 机器
sudo 密码
Virbox 授权码
```

## 2. 登录 Ubuntu

```bash
ssh 用户名@目标机IP
```

## 3. 一键安装

```bash
BASE="https://download.cflank-trade.top/delivery"
LICENSE_KEY_FILE="$HOME/.claw-trade/license.key"

mkdir -p "$(dirname "${LICENSE_KEY_FILE}")"
umask 077
printf '%s\n' '这里粘贴 Virbox 授权码' > "${LICENSE_KEY_FILE}"
sudo apt-get update
sudo apt-get install -y ca-certificates curl
curl -fsSL "${BASE}/install_from_r2_ubuntu.sh" | bash -s -- --base-url "${BASE}" --license-key-file "${LICENSE_KEY_FILE}"
```

脚本会自动做这些事：

```text
安装基础命令。
读取 delivery/latest.txt 解析最新版本。
检查 split_lock_detect=off；已开启就跳过，未开启就写 grub 并重启。
下载并安装 Virbox LCC。
下载生产包和 sha256，校验通过后安装。
绑定 Virbox 授权码。
安装更新公钥和更新 helper。
启动 control runtime 和 UI。
验证 factory seed、UI、auto-update 命令。
```

如果脚本因为 split lock 重启机器，重新登录后再执行上面同一条 `curl -fsSL ... | bash` 命令。不要执行 `/tmp/install_from_r2_ubuntu.sh`，重启后 `/tmp` 里的脚本文件可能不存在。

## 4. 指定版本安装

通常不用指定版本。需要固定版本时：

```bash
curl -fsSL "${BASE}/install_from_r2_ubuntu.sh" | bash -s -- --base-url "${BASE}" \
  claw-trade-production-1.0.0-20260706T053438Z \
  --license-key-file "${LICENSE_KEY_FILE}"
```

## 5. 验证

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5175/
curl -fsS http://127.0.0.1:5175/api/ui/get-license-status
readlink /opt/claw-trade/current
sudo grep CLAW_TRADE_UPDATE_BASE_URL /opt/claw-trade/shared/config/claw-trade.env
sudo grep CLAW_TRADE_AUTO_UPDATE_INSTALL /opt/claw-trade/shared/config/claw-trade.env
ls -l /etc/claw-trade/update-signing-public.pem
systemctl --failed --no-pager
```

通过标志：

```text
第一条输出 200。
license status 里有 "status":"activated"。
current 指向 /opt/claw-trade/releases/claw-trade-production-版本-时间。
CLAW_TRADE_UPDATE_BASE_URL=https://download.cflank-trade.top/stable/。
CLAW_TRADE_AUTO_UPDATE_INSTALL=1。
update-signing-public.pem 存在。
systemctl --failed 输出 0 loaded units listed。
```

说明：不要把 `claw-trade-auto-update.timer` 当作成功标志。受保护 Python 在 systemd service 下会崩溃；自动更新由 UI 进程内调度器执行，安装脚本会验证 `claw-trade-auto-update` 命令本身可运行。

## 6. 打开页面

在同一局域网电脑浏览器打开：

```text
http://目标机IP:5175/
```

目标机查 IP：

```bash
ip -4 addr
```
