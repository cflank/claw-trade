# CoinGlass 失败问题总结

## 现象

BTC 分析时，CoinGlass 相关数据大量失败，返回类似：

```text
provider returned HTTP 404: Endpoint not found
```

受影响的数据包括：

- 衍生品：持仓量、资金费率、清算历史、多空比
- 清算地图：liquidation map
- 链上：交易所余额、巨鲸转账、SOPR、NUPL、活跃地址
- 事件：unlock、vesting、news

## 根因

本地 `.env` 里的 `COINGLASS_API_BASE` 写错了。

错误配置把基础地址写成了一个具体接口：

```text
COINGLASS_API_BASE=https://proxy.keystore.com.cn/api/v1/proxy/coinglass/api/futures/supported-coins
```

但代码会在 `COINGLASS_API_BASE` 后面继续拼接 `/v4/api/...`。

所以最终请求会变成错误路径，例如：

```text
.../coinglass/api/futures/supported-coins/v4/api/futures/open-interest/exchange-list
```

CoinGlass 代理找不到这个路径，因此返回 404。

## 正确配置

`COINGLASS_API_BASE` 应该只写代理根地址：

```text
COINGLASS_API_BASE=https://proxy.keystore.com.cn/api/v1/proxy/coinglass
```

认证 header 仍然是：

```text
COINGLASS_API_HEADER_NAME=X-Api-Key
```

不要把 API key 写进文档。

## 已做修复

已把 `.env` 中的 `COINGLASS_API_BASE` 改为代理根地址。

## 验证

已运行：

```text
pnpm vitest run tests/live.test.ts
```

结果：

```text
30 passed
```

## 后续动作

需要重启 BB MCP / Codex，让运行中的 MCP 进程重新加载 `.env`。

重启后再跑 BTC live 查询，确认 CoinGlass 数据是否恢复。

如果仍有 404，再按 KeyStore 的完整接口规范逐个核对 endpoint：

```text
https://www.keystore.com.cn/api/v1/modules/coinglass/api-spec
```
