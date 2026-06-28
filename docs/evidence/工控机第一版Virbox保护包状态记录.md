# 工控机第一版 Virbox 保护包状态记录

- 记录日期：2026-06-27
- 当前结论：未产出 Virbox Protector/DSProtector 加壳保护包；已产出带 Virbox 运行时授权检查的客户测试交付包。该包包含普通软件、A 股历史 seed、CRYPTO 历史 seed、包内 MongoDB runtime 和 Virbox SDK 状态命令，但仍不是加壳包；官方 Virbox 保护流程/Protector 授权仍需继续跑通。

## 已确认

- 当前已构建的 `.runtime/production-build/claw-trade-test-task10.tar` 是普通启动验证包，不是完整报告生产包，也不是 Virbox Protector 加壳/保护后的交付包。
- 实查该包不包含顶层 `agents/` 和 `openclaw_plugins/`；生产启动脚本也没有生成 OpenClaw worker 配置。因此它只能证明 UI/基础服务启动，不能证明 `/report` 完整报告链路可运行。
- 当前代码层已修下一版普通生产包基线：
  - `scripts/production/build_production_package.sh` 会把 `agents/` 和 `openclaw_plugins/` 打入 `runtime/assets/agents.tar` 与 `runtime/assets/openclaw_plugins.tar`。
  - `packaging/production/runtime/claw-trade-control-runtime` 会在启动时解包运行资产到 `${CLAW_TRADE_RUNTIME_ASSETS_ROOT:-/opt/claw-trade/shared/runtime-assets}`。
  - `src/claw_trade/production/openclaw_config.py` 会基于运行资产生成 `OPENCLAW_CONFIG_PATH` 指向的 OpenClaw 配置。
  - `scripts/production/audit_production_package.py` 和 `src/claw_trade/production/preflight.py` 已要求运行资产存在。
  - 这不是加密，只是修普通生产包；正式 Virbox 包仍要走官方解释器保护和 DSProtector。
- 已重做普通生产包：
  - 路径：`.runtime/production-build/claw-trade-factory-test-runtime-assets-20260627.tar`
  - SHA256：`4aa67ce55988cfbae3b3f4c80d8c4a4cc28fea2fbb1f3eb18fe57734041c20a7`
  - 结构审计：通过；包内有 `runtime/assets/agents.tar` 和 `runtime/assets/openclaw_plugins.tar`，release 顶层没有 `agents/` 和 `openclaw_plugins/`。
  - smoke 预检和 OpenClaw 配置生成：通过。
  - 尚未完成：目标 Ubuntu 无开发仓库真实 `/report` 验收；Virbox 官方保护流程。
- 已产出客户测试交付包：
  - 路径：`.runtime/customer-delivery/claw-trade-customer-delivery-20260627T091541Z.tar`
  - SHA256：`40c81e00623cba7baed66b3e5d1a8c941bf75f6c6c209b0cdc533cabbdeddd43`
  - 软件 tar SHA256：`454fb945acf1b94f9d394b1165096a6786b3b2371f7990bd74b120178c60c77d`
  - 内容：普通软件生产包、包内 MongoDB runtime、A 股历史 seed、CRYPTO 历史 seed、校验文件、`install-all.sh`、已验证安装/卸载脚本、历史数据恢复脚本、恢复验证 evidence、交付操作 README。
  - 数据范围：A 股 `daily_bar` 和 `valuation_metric`，各 1,626,559 行，覆盖 2025-03-03 到 2026-05-27。
  - CRYPTO 数据范围：USDT-only 596 个 symbol，`daily_bar` 355,791 行，1h `intraday_bar` 8,536,045 行，覆盖 2025-06-01 到 2026-06-06。
  - 安装方式：A 股和 CRYPTO seed metadata 恢复进同一个 Mongo seed 库 `claw_trade_factory_seed`。
  - 真机验收：Ubuntu `192.168.1.21` 从无 `27017/1933/18789/5175` 监听状态执行 `scripts/install-all.sh` 通过；包内 MongoDB 自动启动；UI 本机和局域网访问均返回 `200`。
  - 不包含：UI secrets、开发运行库、provider cache。
  - 保护状态：不是 Virbox 保护包。
- 已产出带 Virbox 授权检查的客户测试交付包：
  - 路径：`.runtime/customer-delivery/claw-trade-customer-delivery-virbox-auth-20260627T213915Z.tar`
  - SHA256：`7f29c93203cacab55872f1f9cb08ccab494e64db84c51859af579085e3d3e201`
  - 软件 tar SHA256：`42546df53e656233088ce0138cba44be830cdc01b4d4e324317cece3c314538d`
  - Virbox SDK 状态命令包 SHA256：`8b59188fdd9083970ac4c3c2b560b4362c57da3e125c657650529755dd3f8984`
  - 内容：沿用上一版完整客户测试交付物，并增加 `virbox/virbox-status-sdk-linux-x86_64.tgz`；安装脚本会安装 SDK 状态命令并写入授权检查环境变量。
  - 不包含：完整授权码、账号密码、API 密码、Virbox Protector 工具授权。
  - 保护状态：不是 Virbox Protector/DSProtector 加壳包，只是运行时授权门已接入。
  - 验证状态：目标 Ubuntu `192.168.1.21` 已用该外层 tar 从零安装验收通过；UI 本机和局域网访问返回 `200`，授权接口返回 `activated`。
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
- 已从 Windows Virbox SDK 工具盒取得 SDK/API：
  - 路径：`C:\Program Files (x86)\senseshield\sdk\API`
  - Linux SDK：`linux-2.4.0.54102-0300000000000009-sdk`
  - Linux C SDK 包含 `ss_lm_control.h`、`ss_lm_runtime.h`、`lib64/libslm_control.so`、`lib64/libslm_runtime.so` 和官方 sample。
- 已新增真实 SDK 状态命令：
  - 源码：`techlab/virbox_probe/virbox_status_sdk.c`
  - 构建脚本：`techlab/virbox_probe/build_virbox_status_sdk.sh`
  - 本机探针包：`.runtime/virbox-status-sdk/virbox-status-sdk-linux-x86_64.tgz`
  - SHA256：`8b59188fdd9083970ac4c3c2b560b4362c57da3e125c657650529755dd3f8984`
  - 行为：调用官方 Virbox Control API 读取本机许可状态；未连接 Virbox LCC 时返回 `runtime_unavailable`，不会输出假授权成功。
- 用户已提供测试授权码；完整授权码不写入仓库、证据文件、README、日志或交付包。
- 在本机构建侧尝试绑定该授权码失败，Virbox 返回“软锁指纹生成失败”。该失败来自仓库 `.runtime` 下的临时 Linux runtime，不能替代目标 Ubuntu 的正式安装验证。
- 目标 Ubuntu 已完成已激活状态验证：许可 ID `16427`、授权码后缀 `6ZSN`、`virbox_status_sdk` 返回 `activated`，UI `/api/ui/get-license-status` 返回允许报告和数据刷新。过期、吊销、未激活、宽限等状态尚未验证。
- 已修复安装/卸载脚本的旧 UI 清理问题：真实 UI 命令是 `/opt/claw-trade/current/runtime/python/bin/python -m claw_trade.web.app ...`，旧脚本只匹配 `python3.12 -m claw_trade.web.app`，导致从零安装时 `5175` 端口被旧进程占用。现已改为匹配 `-m claw_trade.web.app`。
- 已发现并修复生产启动脚本问题：`runtime.env` 原本没有被 `claw-trade-ui` / `claw-trade-control` 读取，导致 UI 即使写了 Virbox 状态命令也拿不到环境变量。
- 已新增实验脚本：`scripts/production/build_virbox_protected_package.sh`。
- 使用现有普通包演练保护流程时，`pyprotector_con` 已能连接 Virbox 服务，但真实返回：

```text
ERROR     error code:A0000011
ERROR     License not found, please login your user account or plug in the USB lock and check the license again
error: Virbox Protector license not found; log in or bind a valid Protector license in Virbox LCC before building the protected package
```

因此当前没有生成正式 `*-virbox-protected.tar`，这是正确阻断。即使后续 Protector 授权补齐，也必须先用最新代码重做普通生产包，并在目标 Ubuntu 上证明 `/report` 不依赖开发仓库后，才能把该普通生产包作为正式加壳输入。

## 官方文档核对结果

本节只记录官方文档和本机命令 help 已确认的信息，不把推断写成事实。

官方来源：

- Virbox 开发者工具盒：`https://h.virbox.com/docs/usermanual/tools/VirboxLM-SDK-Toolkits/`
- 授权码 Python 加密流程：`https://h.virbox.com/docs/slock-licensekey/QuickStart3-protection-process/Protect-Python-apps/`
- 云许可 Python 加密流程：`https://h.virbox.com/docs/cloud-license/QuickStart3-protection-process/Protect-Python/`
- Linux 环境使用 VBP LM 加壳：`https://h.virbox.com/docs/faq/Protect_APP_inLinux/`
- VBP LM 命令行 `.ssp` 说明：`https://h.virbox.com/docs/faq/Visual-Studio-post-build-event+vbp-command-line/`

已确认官方流程：

1. Virbox 开发者工具盒用于在线下载 SDK、工具、文档、API 和示例代码；首次安装使用需要登录开发者账号更新 SDK。
2. Linux 环境使用 VBP LM 时，先从 Windows 工具盒下载的 SDK 中复制 `sdk/API/linux` 到 Linux。
3. 在 Linux 的 `sdk/API/linux` 目录给 `copy_lib_share.sh` 执行权限并运行，脚本会把 runtime 库放到 `/usr/local/share/senseshield/sdk/`，供 VBP LM 加壳时查找。
4. Linux 版 VBP LM 目前官方说明为 x86_64 平台。
5. Python 官方保护流程是：先用 Virbox Protector 对运行环境里的 `python` 解释器加壳，再用 DSProtector 对 `.py` 或 `.pyc` 加密。
6. 授权码模式下，许可类型选择“软锁-本地许可”，许可 ID 要与创建产品时的 ID 一致；当前测试产品许可 ID 为 `16427`。
7. 官方 Python 文档要求：`导入表保护`、`压缩`、`资源保护` 不选；打开 DS 按钮并设置密码。
8. 保护解释器成功后会生成 `protected/` 目录和 `.ssp` 文件；`.ssp` 是下一步 DSProtector 加密 `.py/.pyc` 使用的配置文件。
9. 命令行加壳入口是 `virboxprotector_con`；官方 Linux 文档明确说明命令行不能设置加密选项，必须先用界面配置并生成 `.ssp` 文件。命令行未指定 `.ssp` 时会在待加壳程序当前路径查找；没有 `.ssp` 会报错，可用 `-x <ssp路径>` 指定。
10. 当前目标机上 `pyprotector_con --help` 确认存在 Python 专用命令行工具，支持 `--install`、`--target-python-version`、`--platforms`、`--interpreter`、`-x`、`-o` 等参数；但它是否足以替代官方“解释器 + DSProtector”交付流程，仍必须以官方 SDK/授权和样机验收结果为准。

当前文档结论：

- 现有 `scripts/production/build_virbox_protected_package.sh` 直接调用 `pyprotector_con` 保护 `site-packages/claw_trade`，只能作为已安装 Python 专用命令的实验性路径。
- 正式 Virbox 保护交付包必须补齐官方流程证据：SDK 复制、Linux VBP LM、解释器保护、`.ssp`、DSProtector 输出、无授权拦截、有授权启动。
- 当前优先操作路径可以放在 Win11 Virbox 开发者工具盒中完成：把 Linux 生产包里的 `runtime/python/bin/python` 作为待保护文件，生成 `protected/` 和 `.ssp` 后再回填到 Linux 包。Linux 本机加壳路径只在需要本机构建或命令行自动化时使用。
- 没有 Protector 工具授权或 `.ssp` 时，不得把普通包或实验包称为正式 Virbox 保护包。

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

当前带 Virbox 授权检查的客户测试交付包给厂家的操作入口已经写入包内 `README.md`。测试安装只执行：

```bash
cd /tmp
tar -xf claw-trade-customer-delivery-virbox-auth-20260627T213915Z.tar
cd claw-trade-customer-delivery-virbox-auth-20260627T213915Z
bash scripts/install-all.sh
curl http://127.0.0.1:5175/ | head
curl http://127.0.0.1:5175/api/ui/get-license-status
```

卸载测试安装：

```bash
bash scripts/uninstall_factory_test_ubuntu.sh --purge-shared
```

下面是正式 Virbox 保护交付包制作流程。

0. 先生成完整报告生产包：包内必须包含报告运行所需的 agent 配置、prompt/stage/skill 资产和 OpenClaw 插件，或包含等价的受保护运行资产；并且在无开发仓库环境下 `/report` 能进入真实 OpenClaw worker 链路。
1. Windows 开发者工具盒登录 Virbox 开发者账号，下载或更新 SDK。
2. 从工具盒 SDK 中复制 `sdk/API/linux` 到 Ubuntu x86_64 构建机。
3. 在 Ubuntu 的 `sdk/API/linux` 目录执行 `sudo chmod 755 copy_lib_share.sh` 和 `sudo ./copy_lib_share.sh`。
4. 安装 Linux x86_64 版 VBP LM / Protector，并记录安装包、版本、sha256。
5. 确认构建机具备 Protector 工具授权；运行授权码只证明客户侧运行许可，不等于工具授权。
6. 用官方界面保护生产包内的 `runtime/python/bin/python`，许可类型选“软锁-本地许可”，许可 ID 填 `16427`，API 密码只人工输入工具。
7. 按官方 Python 文档设置：`导入表保护`、`压缩`、`资源保护` 不选，打开 DS 按钮并设置 DS 密码。
8. 生成 `protected/` 和 `.ssp`，保留原始解释器用于回滚和对比。
9. 用 DSProtector 选择 `.ssp`，保护需要交付的 `.py` 或 `.pyc` 文件/目录。
10. 用 `protected/python` 和 DS 输出替换生产包对应文件。
11. 如果使用命令行自动化，必须指定或放置已由界面生成的 `.ssp`；没有 `.ssp` 或授权时必须失败。
12. 验证保护后文件无授权被拦截、有授权可启动。
13. 用保护后文件重新打生产交付包。
14. 审计交付包不含源码、`.git`、测试、项目文档、未保护原始程序、账号/API 密码/token/私钥；prompt/stage/skill 资产不得以未保护明文方式暴露给厂家，除非本轮明确接受该风险。
15. 在 Ubuntu 工控机上验收授权阻断和真实报告路径。

## 保护包构建脚本行为

`scripts/production/build_virbox_protected_package.sh` 的真实行为（实验路径，不是正式交付路径）：

1. 没有传入 `CLAW_TRADE_INPUT_ARCHIVE` 时，先调用普通生产包构建脚本。
2. 解开普通生产包。
3. 定位 release 内的 `runtime/python/**/site-packages/claw_trade`。
4. 调用 `pyprotector_con` 保护 `claw_trade` 包目录。
5. 只有保护输出中存在 `virbox_pyruntime` 时，才替换 release 内的原始 `claw_trade` 目录。
6. 重新打包为 `.runtime/production-build/claw-trade-<version>-virbox-protected.tar`。
7. 运行现有生产包审计。
8. 生成 `.sha256`。

该脚本不会在授权缺失时产出假保护包。当前普通生产包基线已开始解决运行资产和 OpenClaw worker 配置问题，但该脚本仍是实验路径，没有按官方完整流程处理解释器保护和 DSProtector 加密。

## 禁止项

- 不得把普通 tar 包称为 Virbox 保护包。
- 不得在没有真实 Protector 的情况下写假加密命令。
- 不得用固定 JSON 或 mock 授权状态冒充 Virbox runtime。
- 不得把缺少报告运行资产、只能打开 UI 的包称为完整生产包。
- 不得把明文 prompt/stage/skill 资产交付给不可信厂家，除非本轮明确接受该风险并在交付记录中写明。
