# 工控机第一版 VM 启动验收记录

- 记录时间：2026-06-27
- 当前结论：未完成 VM 启动验收
- archive：`.runtime/production-build/claw-trade-test-task10.tar`
- sha256：`50c22709827537a283b968288784057233bf5660414cc13d8bfa514271b5a1de`
- 安装脚本：`scripts/production/install_production_package.sh`

## 已完成

- 已创建生产包安装脚本。
- 已执行安装脚本语法检查：

```bash
bash -n scripts/production/install_production_package.sh
```

结果：退出码 0。

## VM 自动验收阻塞

本轮没有写入“VM 通过”结论，因为目标机自动访问没有打通。

本机 `virsh` 探测：

```bash
command -v virsh
```

结果：退出码 1，当前环境没有可用 `virsh` 命令。

候选 Ubuntu 机器 SSH 探测：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 frank@192.168.1.21 'hostname && python3 --version && id'
```

结果：

```text
frank@192.168.1.21: Permission denied (publickey,password).
```

含义：当前会话没有 `frank@192.168.1.21` 的免密 SSH 凭据，不能自动复制生产包、安装到 `/opt/claw-trade`、启动服务或采集 health 结果。

## 待补验收项

- Ubuntu 版本：未采集
- 安装命令：未在 VM 执行
- preflight 结果：未执行
- control runtime health：未执行
- UI healthz：未执行
- Virbox 探针路径和脱敏输出：未执行
- 禁止目录检查：未执行
- systemd verify：未在目标机执行

## 下一步

在目标 Ubuntu 机器可输入密码的终端中执行后续复制和安装命令，或为 `frank@192.168.1.21` 配置临时 SSH key 后由 Codex 继续自动验收。
