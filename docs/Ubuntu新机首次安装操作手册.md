# Ubuntu 新机首次安装操作手册

版本：

```bash
REL="claw-trade-production-1.0.0-20260706T053438Z"
BASE="https://download.cflank-trade.top/delivery"
```

## 1. 准备

准备好：

```text
Ubuntu 24.04 机器
sudo 密码
Virbox 授权码
Virbox LCC Ubuntu 安装包：senseshield-lcc-*.deb
```

## 2. 登录 Ubuntu

```bash
ssh 用户名@目标机IP
```

## 3. 安装基础命令

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
```

## 4. 安装 Virbox LCC

先把 `senseshield-lcc-*.deb` 放到目标机 `/tmp`。

```bash
cd /tmp
ls -lh senseshield-lcc-*.deb
sudo apt-get install -y ./senseshield-lcc-*.deb
```

检查 LCC 服务：

```bash
curl -fsS -X POST http://127.0.0.1:12339/v1/license/enumLicense \
  -H 'Content-Type: application/json' \
  -d '{}'
```

这条失败，就先处理 Virbox LCC，成功后再继续。

## 5. 设置 split lock

```bash
grep 'split_lock_detect=off' /proc/cmdline
```

如果上面没有输出，执行：

```bash
sudo cp -a /etc/default/grub "/etc/default/grub.bak-claw-trade-$(date -u +%Y%m%dT%H%M%SZ)"
grep -q 'split_lock_detect=off' /etc/default/grub || sudo sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\([^"]*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 split_lock_detect=off"/' /etc/default/grub
sudo update-grub
sudo reboot
```

重启后重新登录，再确认：

```bash
grep 'split_lock_detect=off' /proc/cmdline
```

## 6. 下载安装

```bash
REL="claw-trade-production-1.0.0-20260706T053438Z"
BASE="https://download.cflank-trade.top/delivery"

cd /tmp
curl -fL -o install_from_r2_ubuntu.sh "${BASE}/install_from_r2_ubuntu.sh"
bash /tmp/install_from_r2_ubuntu.sh "$REL"
```

提示输入 Virbox 授权码时，粘贴授权码并回车。

## 7. 验证

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5175/
curl -fsS http://127.0.0.1:5175/api/ui/get-license-status
grep CLAW_TRADE_UPDATE_BASE_URL /opt/claw-trade/shared/config/claw-trade.env
grep CLAW_TRADE_AUTO_UPDATE_INSTALL /opt/claw-trade/shared/config/claw-trade.env
ls -l /etc/claw-trade/update-signing-public.pem
```

通过标志：

```text
第一条输出 200
license status 里有 "status":"activated"
update-signing-public.pem 存在
```

## 8. 打开页面

在同一局域网电脑浏览器打开：

```text
http://目标机IP:5175/
```

目标机查 IP：

```bash
ip -4 addr
```
