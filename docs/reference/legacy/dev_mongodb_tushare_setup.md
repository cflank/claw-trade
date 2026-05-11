# Development MongoDB and Tushare Setup

This document is for local development only. Production should provide MongoDB as an external service and inject secrets through the deployment environment.

## MongoDB

Start local MongoDB:

```bash
scripts/start-local-mongodb.sh
```

Default local settings:

```bash
CN_A_MONGODB_URI=mongodb://127.0.0.1:27017
CN_A_MONGODB_DATABASE=claw_trade
CN_A_MONGODB_CACHE_COLLECTION=cn_a_fundamental_cache
```

Stop local MongoDB:

```bash
scripts/stop-local-mongodb.sh
```

Local MongoDB files live under `.runtime/mongodb/`. That directory is ignored by git.

## Tushare Token

For claw-trade local development, put the token in:

```text
.env.local
```

You can start from the tracked template:

```bash
cp .env.example .env.local
```

Then fill:

```bash
TUSHARE_TOKEN=your_token_here
# Optional: only set when using a temporary private Tushare proxy URL.
# TUSHARE_HTTP_URL=https://your-private-tushare-proxy.example
# CN_A_TUSHARE_HTTP_URL=https://your-private-tushare-proxy.example
CN_A_MONGODB_URI=mongodb://127.0.0.1:27017
CN_A_MONGODB_DATABASE=claw_trade
CN_A_MONGODB_CACHE_COLLECTION=cn_a_fundamental_cache
```

Tushare initialization is centralized in:

```text
src/claw_trade/providers/tushare_client.py
```

All Tushare callers should use `create_tushare_pro(...)` instead of calling
`ts.pro_api(...)` directly. By default this keeps the SDK standard endpoint and
does not set private HTTP URL. `pro._DataApi__http_url` is set only when
`TUSHARE_HTTP_URL` or `CN_A_TUSHARE_HTTP_URL` is explicitly configured. The token
must stay in environment variables; do not write a real token into code, docs, or
checked-in config.

If you see token invalid errors, first verify the runtime token source and value
in environment, then check whether your environment requires a private proxy URL.

The runtime load order is:

```text
current shell environment > claw-trade .env.local > ~/.openclaw/.env
```

Do not commit real tokens or database passwords to the repository.
