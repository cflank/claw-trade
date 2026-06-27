# 工控机第一版 Virbox 保护包状态记录

- 记录日期：2026-06-27
- 当前结论：未产出 Virbox 保护包；阻塞点是本机缺少 Virbox Protector 授权

## 已确认

- 当前已构建的 `.runtime/production-build/claw-trade-test-task10.tar` 是普通生产包，不是 Virbox Protector 加壳/保护后的交付包。
- 已从 Virbox 官方下载 Linux x86_64 工具包，保存到仓库外的 `~/secure-vendor/virbox/linux-x86_64/`：
  - Protector 试用版：`protector/virboxprotector_trial_3.5.6.22633.deb`
    - SHA256：`561942ccb49fda9f6fa87908cc2342726e731ba118fd7ddeb685730526b858c4`
  - Python 扩展包：`python-extension/python_extension.zip`
    - SHA256：`ca2f7aa4cba2f53edd8f38f9986e735ae2c4b4dfdfbe4bea0cb61d5788f843e7`
  - Virbox 用户工具/runtime：`runtime/senseshield-lcc-2.7.5.69040-amd64.deb`
    - SHA256：`6b17a673ceb3a3b1315b2445c3c8ac9469a799f1b7b0c77e6ccdd75c8ab22a51`
- 官方 Protector 试用版 `.deb` 内包含命令行工具：
  - `bin/pyprotector_con`
  - `bin/virboxprotector_con`
- 官方 Python 扩展包版本为 `2.5`，声明支持 Python `3.6` 到 `3.14`。
- `pyprotector_con` 实际查找 Python 扩展包的路径是 `/home/frank/virboxLMProtector/python_extension`。
- 已创建用户级链接：`/home/frank/virboxLMProtector/python_extension -> <worktree>/.runtime/virbox/protector/usr/share/virboxprotector-trial/python_extension`。
- 已在沙箱外启动 Virbox 用户服务：
  - SenseShield Service：Running
  - 版本：`2.7.5.69040`
  - 本地端口：`10334`
  - LCC 端口：`12339`
- 本地 LCC 许可枚举接口可用：
  - `POST http://127.0.0.1:12339/v1/license/enumLicense`
  - 当前返回：`[]`
- 已新增脚本：`scripts/production/build_virbox_protected_package.sh`。
- 使用现有普通包演练保护流程时，`pyprotector_con` 已能连接 Virbox 服务，但真实返回：

```text
ERROR     error code:A0000011
ERROR     License not found, please login your user account or plug in the USB lock and check the license again
error: Virbox Protector license not found; log in or bind a valid Protector license in Virbox LCC before building the protected package
```

因此当前没有生成 `*-virbox-protected.tar`，这是正确阻断。

## 当前必须完成的人工授权动作

二选一：

1. 打开本机 Virbox LCC：`http://127.0.0.1:12339/#/license`
   - 添加许可。
   - 选择云账号登录，输入 Virbox 账号和密码；或选择授权码绑定，输入 Virbox 提供的 Protector/试用授权码。
   - 不把账号密码或完整授权码写入仓库。
2. 使用命令行登录或绑定：
   - 云账号登录接口：`POST /v1/license/accountLogin`，请求体为 `{"account":"...","password":"..."}`。
   - 授权码绑定接口：`GET /v1/license/bindLicenseKey`，参数为 `licenseKey` 和可选 `password`。
   - 密码和完整授权码只能人工输入，不能提交到仓库或日志。

授权完成后，先验证：

```bash
/home/frank/src/claw-trade/.worktrees/virbox-delivery-implementation/.runtime/virbox/runtime/opt/senseshield/ssclt -l all
```

再重新执行：

```bash
CLAW_TRADE_INPUT_ARCHIVE=/home/frank/src/claw-trade/.worktrees/virbox-delivery-implementation/.runtime/production-build/claw-trade-test-task10.tar \
VIRBOX_PROTECTOR_BIN=/home/frank/src/claw-trade/.worktrees/virbox-delivery-implementation/.runtime/virbox/protector/usr/share/virboxprotector-trial/bin/pyprotector_con \
VIRBOX_PYTHON_EXTENSION_DIR=/home/frank/virboxLMProtector/python_extension \
scripts/production/build_virbox_protected_package.sh test-task10
```

## 正确原子流程

1. 下载并记录 Virbox Protector Linux 版、Virbox Linux runtime、Virbox SDK/示例程序。
2. 根据 Virbox 官方文档确认 Python 保护输入格式。
3. 先生成可直接运行的“待保护程序”。
4. 验证待保护程序未加壳前可以启动。
5. 使用 Virbox Protector 加壳，输出独立保护后文件。
6. 验证保护后文件无授权被拦截、有授权可启动。
7. 用保护后文件重新打生产交付包。
8. 审计交付包不含源码、`.git`、测试、文档、未保护原始程序、账号/token/私钥。
9. 在 Ubuntu 工控机上验收授权阻断和真实报告路径。

## 保护包构建脚本行为

`scripts/production/build_virbox_protected_package.sh` 的真实行为：

1. 没有传入 `CLAW_TRADE_INPUT_ARCHIVE` 时，先调用普通生产包构建脚本。
2. 解开普通生产包。
3. 定位 release 内的 `runtime/python/**/site-packages/claw_trade`。
4. 调用 `pyprotector_con` 保护 `claw_trade` 包目录。
5. 只有保护输出中存在 `virbox_pyruntime` 时，才替换 release 内的原始 `claw_trade` 目录。
6. 重新打包为 `.runtime/production-build/claw-trade-<version>-virbox-protected.tar`。
7. 运行现有生产包审计。
8. 生成 `.sha256`。

该脚本不会在授权缺失时产出假保护包。

## 禁止项

- 不得把普通 tar 包称为 Virbox 保护包。
- 不得在没有真实 Protector 的情况下写假加密命令。
- 不得用固定 JSON 或 mock 授权状态冒充 Virbox runtime。
