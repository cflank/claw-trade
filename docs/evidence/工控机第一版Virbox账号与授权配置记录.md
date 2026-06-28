# 工控机第一版 Virbox 账号与授权配置记录

## 当前结论

- 状态：未完成
- 日期：2026-06-26
- 阻塞：本机未发现 `/home/frank/secure-vendor/virbox/linux-x86_64`，尚不能证明 Linux x86_64 SDK/runtime 已取得。
- 下一步：从 Virbox 控制台或支持方下载 Linux x86_64 SDK/runtime 后，放入安全目录，再补齐本记录。

## 账号申请

- 申请日期：2026-06-26
- 申请方式：试用账号已申请，具体账号凭据不入库
- 工单号或联系人：待填写
- 控制台入口：已记录到安全凭据库，不入库
- 账号密码：不入库

## 测试产品

- 产品名称：claw-trade-prod-v1-test
- 产品 id：待从 Virbox 控制台填写
- 平台：Linux x86_64 / Ubuntu x86_64 工控机
- 授权类型：云许可/软许可
- 授权码模式：一台设备一个唯一授权码
- 心跳频率：1 天
- 离线宽限：30 天

## 功能模块

| 功能 | Virbox 标识 | 用途 |
| --- | --- | --- |
| report_generation | 待填写 | 控制报告生成 |
| data_refresh | 待填写 | 控制数据刷新 |

## 测试授权码矩阵

| 场景 | 授权码短标识 | 状态制造方式 | 预期 report | 预期 data_refresh |
| --- | --- | --- | --- | --- |
| active_001 | 后四位待填写 | 控制台创建或支持方提供 | allow | allow |
| not_activated_001 | 后四位待填写 | 控制台创建或支持方提供 | block | block |
| expired_001 | 后四位待填写 | 控制台设置过期或支持方提供 | block | block |
| revoked_001 | 后四位待填写 | 激活后控制台吊销或支持方提供 | block | block |
| grace_001 | 后四位待填写 | 控制台/网络状态制造离线宽限 | allow | allow |
| over_grace_001 | 后四位待填写 | 控制台/网络状态制造超过宽限 | block | block |
| feature_report_only_001 | 后四位待填写 | 只开通 report_generation | allow | block |
| feature_refresh_only_001 | 后四位待填写 | 只开通 data_refresh | block | allow |

## SDK/runtime

- SDK 版本：未取得
- runtime 版本：未取得
- 本地安全路径：`/home/frank/secure-vendor/virbox/linux-x86_64`
- sha256 记录：待取得 SDK/runtime 后补齐
- 官方状态命令：待取得 SDK/runtime 后写入 `techlab/virbox_probe/README.md`

## 停止条件

- 未取得 Linux x86_64 SDK/runtime 前，不进入授权服务接入。
- 未跑通 `techlab/virbox_probe/virbox_probe.py` 前，不声称 Virbox 验证门通过。
