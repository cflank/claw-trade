# A股个股新闻情报 Agent 设计方案

> 目标：为 OpenClaw 选股管线新增 `news` 工具节点，实现按个股维度的新闻采集、分类、情绪研判、相关性评估、证据缺口分析，输出结构化 JSON 供 `daily_stock_analysis` 消费。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────┐
│                   OpenClaw Pipeline                  │
│                                                      │
│  akshare-stock ──► screener ──► daily_stock_analysis │
│                                       ▲              │
│                                       │              │
│                              ┌────────┴────────┐    │
│                              │  stock_news_tool │    │
│                              └────────┬────────┘    │
│                                       │              │
│         ┌──────────┬──────────┬───────┴────┐        │
│         ▼          ▼          ▼            ▼        │
│    ┌─────────┐┌─────────┐┌────────┐┌───────────┐   │
│    │ 东方财富 ││ 财联社  ││ 新浪   ││ 同花顺    │   │
│    │ 个股新闻 ││ 电报    ││ 财经   ││ 问财      │   │
│    └─────────┘└─────────┘└────────┘└───────────┘   │
│                                                      │
│    采集 → 去重 → LLM加工 → 结构化输出               │
└─────────────────────────────────────────────────────┘
```

整体分四层：

| 层级 | 职责 | 工具/项目 |
|------|------|-----------|
| L1 数据采集 | 多源拉取原始新闻 | AKShare / Tushare / pywencai |
| L2 预处理 | 去重、排序、裁剪 | Python (difflib / URL去重) |
| L3 LLM 加工 | 分类/情绪/相关性/摘要/缺口 | DeepSeek / Qwen / Claude |
| L4 结构化输出 | 标准 JSON 写入管线 | 自定义 schema |

---

## 二、L1 数据采集层 — 各数据源详细说明

### 2.1 AKShare — `stock_news_em`（个股新闻，核心源）

| 属性 | 说明 |
|------|------|
| **项目地址** | https://github.com/akfamily/akshare |
| **文档** | https://akshare.akfamily.xyz/data/stock/stock.html |
| **接口名** | `stock_news_em` |
| **数据来源** | 东方财富搜索接口 http://so.eastmoney.com/news/s |
| **功能** | 按股票代码获取该个股最近的新闻资讯 |
| **返回字段** | 关键词(股票代码)、新闻标题、新闻内容(正文摘要)、发布时间、文章来源、新闻链接 |
| **限制** | 当日最近 200 条新闻（分页可扩展） |
| **费用** | 免费，无需注册 |
| **安装** | `pip install akshare` |

**调用示例：**

```python
import akshare as ak

# 按股票代码获取个股新闻
df = ak.stock_news_em(stock="600519")
# 返回 DataFrame，包含：
# - 关键词    (如 "600519")
# - 新闻标题  (如 "贵州茅台一季度净利同比增长...")
# - 新闻内容  (正文摘要)
# - 发布时间  (如 "2025-04-30 08:15:00")
# - 文章来源  (如 "证券时报")
# - 新闻链接  (http://finance.eastmoney.com/a/xxx.html)
```

**覆盖你的需求：**
- ✅ 按股票代码查新闻
- ✅ 标来源、时间、链接
- ✅ 返回新闻内容摘要
- ❌ 不含情绪分类（需 LLM 加工）
- ❌ 不含新闻类型分类
- ❌ 不含相关性评估

---

### 2.2 AKShare — `stock_telegraph_cls`（财联社电报，最快源）

| 属性 | 说明 |
|------|------|
| **接口名** | `stock_telegraph_cls` |
| **数据来源** | 财联社 https://www.cls.cn/telegraph |
| **功能** | 获取财联社电报快讯（全部 / 重点） |
| **返回字段** | 标题、内容、发布时间 |
| **特点** | 财联社电报是 A 股市场最快的中文新闻源之一，盘中实时更新 |
| **费用** | 免费 |

**调用示例：**

```python
import akshare as ak

# 获取全部财联社电报
df = ak.stock_telegraph_cls(symbol="全部")
# 也可以只获取重点
df = ak.stock_telegraph_cls(symbol="重点")
```

**用途**：作为全局快讯源，需要后续通过股票名称/代码匹配关联到个股。

---

### 2.3 AKShare — 其他资讯接口（多源补充）

| 接口名 | 数据来源 | 说明 |
|--------|----------|------|
| `stock_info_global_em` | 东方财富 | 全球财经快讯 |
| `stock_info_global_sina` | 新浪财经 | 新浪财经快讯 |
| `stock_info_global_futu` | 富途牛牛 | 富途快讯 |
| `stock_info_global_ths` | 同花顺 | 同花顺财经直播 |
| `stock_info_global_cls` | 财联社 | 财联社全球快讯 |
| `stock_info_cjzc_em` | 东方财富 | 财经早餐（每日早间汇总） |
| `index_news_sentiment_scope` | 数库(ScopeDB) | A股整体新闻情绪指数（非个股） |

**`index_news_sentiment_scope` 特别说明：**

| 属性 | 说明 |
|------|------|
| **数据提供方** | 数库科技 https://www.scopedb.cn |
| **功能** | 提供 A 股市场整体的新闻情绪指数 |
| **维度** | 市场级别，非个股级别 |
| **用途** | 可作为宏观情绪背景参考，但不能替代个股情绪分析 |

---

### 2.4 Tushare Pro — 新闻 + 公告

| 属性 | 说明 |
|------|------|
| **项目地址** | https://tushare.pro |
| **GitHub** | https://github.com/waditu/tushare |
| **核心接口** | `pro.news()`（新闻快讯）、`pro.anns()`（上市公司公告）、`pro.major_news()`（重大新闻）、`pro.cctv_news()`（联播新闻） |
| **数据来源** | 多个官方及新闻渠道整合 |
| **费用** | 需注册，需积分（2000+ 积分可较自由使用 A 股数据） |
| **积分获取** | 注册送基础积分，签到/分享/充值获取更多（充值比例 1:10） |
| **安装** | `pip install tushare` |

**调用示例：**

```python
import tushare as ts

pro = ts.pro_api('YOUR_TOKEN')

# 新闻快讯
df = pro.news(src='sina', start_date='20250501', end_date='20250506')
# 返回: datetime, content, title, channels

# 上市公司公告
df = pro.anns(ts_code='600519.SH', start_date='20250401', end_date='20250506')
# 返回: ts_code, ann_date, title, content, pub_time, url
```

**优势**：公告数据（`anns`）是其他免费源不太好获取的，可以直接拿到个股公告原文摘要。

**劣势**：积分限制，高频使用需付费。

---

### 2.5 pywencai — 同花顺问财自然语言查询

| 属性 | 说明 |
|------|------|
| **GitHub** | https://github.com/zsrl/pywencai |
| **数据来源** | 同花顺问财 https://www.iwencai.com |
| **功能** | 用自然语言查询同花顺问财，返回结构化数据 |
| **协议** | MIT（不赞成商用） |
| **费用** | 免费（需低频使用，高频会被封） |
| **安装** | `pip install pywencai` |

**调用示例：**

```python
import pywencai

# 自然语言查询个股新闻
res = pywencai.get(query='贵州茅台最近一周利好新闻')
res = pywencai.get(query='600519最近有什么公告')
res = pywencai.get(query='白酒行业最近的政策新闻')
```

**优势**：
- 可以用自然语言直接问，灵活度极高
- 同花顺的数据覆盖面广，包含研报、公告、新闻
- 适合做行业/政策/宏观新闻的补充查询

**劣势**：
- 高频调用会被封 IP
- 返回格式不稳定，接口策略经常变化
- 需要传入 cookie（登录态）

---

### 2.6 金融界个股新闻（爬虫备选）

| 属性 | 说明 |
|------|------|
| **来源** | 金融界 http://stock.jrj.com.cn/share |
| **接口格式** | `http://stock.jrj.com.cn/share,{股票代码},ggxw.shtml` |
| **返回** | 个股公告新闻列表（HTML 需解析） |
| **费用** | 免费 |
| **示例** | http://stock.jrj.com.cn/share,600519,ggxw.shtml |

这是纯爬虫方案，需要自己解析 HTML（lxml/BeautifulSoup），稳定性一般，但可以作为东方财富的交叉验证源。

---

### 2.7 数据源优先级建议

对于你的选股 Agent 场景，推荐以下优先级：

| 优先级 | 数据源 | 角色 | 原因 |
|--------|--------|------|------|
| P0 | `stock_news_em` | 个股新闻主源 | 免费、按代码查、返回字段完整 |
| P0 | `stock_telegraph_cls` | 快讯主源 | 财联社电报最快，盘中实时 |
| P1 | `stock_info_global_sina` | 全局新闻补充 | 新浪财经覆盖面广 |
| P1 | Tushare `pro.anns()` | 公告数据 | 公告是确定性最高的信息 |
| P2 | pywencai | 灵活补充 | 用于查行业/政策/宏观新闻 |
| P3 | 金融界爬虫 | 交叉验证 | 备用 |

---

## 三、L2 预处理层 — 去重、排序、裁剪

### 3.1 去重策略

```python
import hashlib
from difflib import SequenceMatcher

def dedup_news(news_list: list[dict]) -> list[dict]:
    """
    两阶段去重：
    1. URL 精确去重
    2. 标题相似度去重（阈值 0.8）
    """
    seen_urls = set()
    seen_titles = []
    result = []

    for item in news_list:
        # 阶段1: URL去重
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        seen_urls.add(url)

        # 阶段2: 标题相似度去重
        title = item.get("title", "")
        is_dup = False
        for prev_title in seen_titles:
            ratio = SequenceMatcher(None, title, prev_title).ratio()
            if ratio > 0.8:
                is_dup = True
                break
        if is_dup:
            continue

        seen_titles.append(title)
        result.append(item)

    return result
```

### 3.2 排序

```python
# 按发布时间降序排列（最新在前）
news_list.sort(key=lambda x: x["publish_time"], reverse=True)
```

### 3.3 裁剪

- 保留最近 7 天的新闻（可配置）
- 单次 LLM 调用最多处理 30 条新闻（控制 token 成本）
- 超过 30 条时按时间取最新的 30 条

---

## 四、L3 LLM 加工层 — 分类/情绪/相关性/摘要/缺口

### 4.1 Prompt 设计

```
你是一位专业的A股新闻分析师。

## 任务
给定一只目标股票的信息和一批新闻，你需要对每条新闻进行分析，输出结构化 JSON。

## 目标股票
- 代码: {stock_code}
- 名称: {stock_name}
- 所属行业: {industry}
- 主营业务: {main_business}

## 分析要求
对每条新闻，输出以下字段：

1. **news_type** (新闻类型):
   - `company`: 直接提到目标公司的新闻（业绩/人事/产品/诉讼等）
   - `industry`: 目标公司所在行业的新闻
   - `policy`: 政策法规类新闻（监管/税收/补贴等）
   - `macro`: 宏观经济新闻（央行/GDP/CPI等）

2. **sentiment** (情绪方向):
   - `bullish`: 利好（业绩超预期/获订单/政策扶持等）
   - `bearish`: 利空（业绩下滑/被处罚/行业下行等）
   - `neutral`: 中性（人事变动/常规公告等）
   - `unknown`: 信息不足无法判断

3. **relevance** (与目标股票的相关性):
   - `strong`: 直接提到公司名称/代码/核心产品
   - `medium`: 提到所在行业/竞争对手/上下游
   - `weak`: 仅宏观环境间接影响

4. **summary**: 一句话核心信息（不超过50字）

5. **evidence_gap**: 如存在以下情况，请说明：
   - 新闻缺乏具体数据支撑
   - 消息来源不明或为"据传"
   - 信息可能过时或未经证实
   - 若无缺口，输出 null

## 输出格式
仅输出 JSON 数组，不要输出其他内容：

```json
[
  {
    "news_id": 1,
    "news_type": "company",
    "sentiment": "bullish",
    "relevance": "strong",
    "summary": "贵州茅台一季度净利同比增长15%超预期",
    "evidence_gap": null
  },
  ...
]
```

## 新闻列表
{news_json_array}
```

### 4.2 LLM 选择建议

| 模型 | 适用场景 | 成本 | 说明 |
|------|----------|------|------|
| **DeepSeek-V3** | 日常使用首选 | 极低 (￥1/M tokens) | 中文理解优秀，JSON 输出稳定，性价比最高 |
| **Qwen-Max** | 国内备选 | 低 | 阿里通义千问，中文金融领域理解好 |
| **Claude Sonnet** | 高质量需求 | 中 | 推理质量最高，但成本较高 |
| **Ollama 本地** | 离线/隐私 | 硬件成本 | 可用 Qwen2.5-7B / DeepSeek-V2-Lite |

**推荐**：日常跑批用 DeepSeek-V3（通过 Anspire/AIHubMix 等中转站调用），关键分析用 Claude。

### 4.3 批量处理策略

```python
# 伪代码：批量新闻 LLM 分析
def analyze_news_batch(stock_info: dict, news_list: list[dict]) -> list[dict]:
    """
    一次 LLM 调用处理一批新闻（最多30条）
    避免逐条调用浪费 token
    """
    # 构造输入
    news_for_llm = [
        {"news_id": i+1, "title": n["title"], "content": n["content"][:200],
         "source": n["source"], "time": n["publish_time"]}
        for i, n in enumerate(news_list[:30])
    ]

    prompt = PROMPT_TEMPLATE.format(
        stock_code=stock_info["code"],
        stock_name=stock_info["name"],
        industry=stock_info["industry"],
        main_business=stock_info.get("main_business", ""),
        news_json_array=json.dumps(news_for_llm, ensure_ascii=False)
    )

    # 调用 LLM
    response = llm_client.chat(prompt)

    # 解析 JSON 输出
    analysis = json.loads(response)

    # 合并原始数据和分析结果
    for item in analysis:
        idx = item["news_id"] - 1
        news_list[idx].update(item)

    return news_list
```

---

## 五、L4 结构化输出 — 最终 JSON Schema

### 5.1 单条新闻输出

```json
{
  "news_id": 1,
  "title": "贵州茅台2025年一季度净利润同比增长15.5%",
  "content": "4月30日，贵州茅台发布2025年一季度报告...(摘要)",
  "source": "证券时报",
  "publish_time": "2025-04-30T08:15:00",
  "url": "http://finance.eastmoney.com/a/xxx.html",
  "data_source": "stock_news_em",

  "news_type": "company",
  "sentiment": "bullish",
  "relevance": "strong",
  "summary": "茅台一季度净利同比增15.5%，超市场预期",
  "evidence_gap": null
}
```

### 5.2 完整输出结构

```json
{
  "stock_code": "600519",
  "stock_name": "贵州茅台",
  "industry": "白酒",
  "analysis_time": "2025-05-06T20:30:00",
  "data_sources_used": [
    "stock_news_em",
    "stock_telegraph_cls",
    "stock_info_global_sina"
  ],
  "total_raw_count": 45,
  "after_dedup_count": 32,
  "analyzed_count": 30,

  "sentiment_summary": {
    "bullish": 12,
    "bearish": 5,
    "neutral": 11,
    "unknown": 2,
    "overall": "偏利好"
  },

  "news_by_type": {
    "company": 15,
    "industry": 8,
    "policy": 4,
    "macro": 3
  },

  "data_gaps": [
    "最近3天无行业政策类新闻",
    "公告数据未接入（缺少Tushare积分）"
  ],

  "news_list": [
    {
      "news_id": 1,
      "title": "...",
      "content": "...",
      "source": "证券时报",
      "publish_time": "2025-04-30T08:15:00",
      "url": "http://...",
      "data_source": "stock_news_em",
      "news_type": "company",
      "sentiment": "bullish",
      "relevance": "strong",
      "summary": "茅台一季度净利同比增15.5%",
      "evidence_gap": null
    }
  ]
}
```

### 5.3 数据缺口检测逻辑

```python
def detect_data_gaps(news_list: list, sources_used: list, stock_info: dict) -> list[str]:
    """检测新闻数据的结构性缺口"""
    gaps = []

    # 1. 新闻数量不足
    if len(news_list) < 5:
        gaps.append(f"近期新闻总量偏少（仅{len(news_list)}条），可能存在信息盲区")

    # 2. 新闻类型缺失
    types_found = set(n.get("news_type") for n in news_list)
    for t in ["company", "industry", "policy"]:
        if t not in types_found:
            label = {"company": "公司", "industry": "行业", "policy": "政策"}[t]
            gaps.append(f"缺少{label}类新闻，建议补充查询")

    # 3. 时间断层
    if news_list:
        from datetime import datetime, timedelta
        times = [datetime.fromisoformat(n["publish_time"]) for n in news_list if n.get("publish_time")]
        if times:
            latest = max(times)
            if (datetime.now() - latest).days > 3:
                gaps.append(f"最新新闻已是{(datetime.now() - latest).days}天前，可能存在时效性问题")

    # 4. 数据源缺失
    all_sources = ["stock_news_em", "stock_telegraph_cls", "stock_info_global_sina", "tushare_anns"]
    missing = [s for s in all_sources if s not in sources_used]
    if "tushare_anns" in missing:
        gaps.append("未接入公告数据源（Tushare），可能遗漏重要公告")

    # 5. 情绪方向不明确
    unknown_count = sum(1 for n in news_list if n.get("sentiment") == "unknown")
    if unknown_count > len(news_list) * 0.3:
        gaps.append(f"{unknown_count}条新闻无法判断情绪方向，信息质量待提升")

    return gaps
```

---

## 六、参考项目溯源

### 6.1 LLM4Stock — 最接近的参考实现

| 属性 | 说明 |
|------|------|
| **GitHub** | https://github.com/Yuis1/LLM4Stock |
| **作者** | Yuis1 |
| **核心思路** | AKShare 抓取财联社电报 → 股票名称匹配 → LLM 判断涨跌 |
| **数据存储** | CSV 文件（`data/stock_cls_telegram.csv`） |
| **LLM 对接** | ChatGPT（通过 API2D 中转） |
| **与你需求的差异** | 只做了电报→股票匹配+涨跌预判，没有新闻分类/相关性/多源/去重/缺口分析 |
| **可借鉴的点** | 股票名称匹配逻辑（含历史简称、全称、曾用名） |

**关键文件：**
- `0_初始化股票列表.ipynb` — 从巨潮资讯网获取公司概况和历史简称
- `data/stock_info.csv` — 整合后的股票代码-名称映射表
- `data/stock_cls_telegram.csv` — 电报+匹配股票+LLM分析结果

### 6.2 daily_stock_analysis — 你已在用

| 属性 | 说明 |
|------|------|
| **GitHub** | https://github.com/ZhuLinsen/daily_stock_analysis |
| **作者** | ZhuLinsen |
| **核心功能** | LLM 驱动的 A/H/美股自选股智能分析，多数据源行情 + 实时新闻 + LLM 决策仪表盘 + 多渠道推送 |
| **新闻能力** | 有实时新闻接入，但新闻分析颗粒度不够细 |
| **LLM 支持** | Anspire、AIHubMix、Gemini、OpenAI、DeepSeek、通义千问、Claude、Ollama |
| **与你需求的关系** | 作为最终消费者，`stock_news_tool` 的输出应该 feed 进 daily_stock_analysis 的分析流程 |

### 6.3 StockSight — 英文情感分析参考

| 属性 | 说明 |
|------|------|
| **GitHub** | https://github.com/shirosaidev/stocksight |
| **功能** | 利用 Elasticsearch 存储 Twitter 和新闻数据，用 NLP 做情感分析 |
| **局限** | 仅支持英文、Twitter（已更名X），不适用 A 股 |
| **可借鉴** | 新闻-情绪-股价关联的架构思路 |

### 6.4 中文金融情感词典（CFSD）— 轻量备选

| 属性 | 说明 |
|------|------|
| **来源** | 学术论文《中文金融领域情感词典构建》 |
| **格式** | CSV 文件，含正面词和负面词两个列表 |
| **用途** | 不依赖 LLM 的轻量级情感判断，可作为 LLM 不可用时的降级方案 |
| **局限** | 词袋模型，无法理解上下文，准确率远低于 LLM |

### 6.5 VADER — 通用情感分析工具

| 属性 | 说明 |
|------|------|
| **PyPI** | `pip install vaderSentiment` |
| **GitHub** | https://github.com/cjhutto/vaderSentiment |
| **功能** | 基于规则的英文情感分析，输出 -1 到 +1 的情感分数 |
| **局限** | 不支持中文，需配合翻译使用，不推荐用于 A 股 |

---

## 七、与 OpenClaw 集成方案

### 7.1 作为 MCP Tool 注册

在 OpenClaw 的 SOUL.md 中将 `stock_news_tool` 标记为 auto-approve，与现有的 akshare-stock 工具并列：

```yaml
# SOUL.md 片段
tools:
  - name: stock_news_tool
    description: "获取并分析个股新闻情报"
    auto_approve: true
    input:
      stock_code: string     # 如 "600519"
      stock_name: string     # 如 "贵州茅台"
      industry: string       # 如 "白酒"
      days: int              # 回溯天数，默认7
    output: NewsReport       # 上述 JSON Schema
```

### 7.2 在三层管线中的位置

```
akshare-stock (行情+资金流)
      │
      ▼
  screener (选股策略: 量价突破/均线/RPS/资金流/基本面/热门板块)
      │
      ├── 筛选出候选股票列表
      │
      ▼
stock_news_tool (对每只候选股票做新闻分析)  ← 新增
      │
      ▼
daily_stock_analysis (综合分析: 行情+基本面+新闻 → AI决策)
```

### 7.3 Cron 调度

```bash
# 在现有的盘后 cron 任务中追加 news 步骤
# 假设收盘后 15:30 开始跑
30 15 * * 1-5 cd /path/to/pipeline && python run_screener.py
35 15 * * 1-5 cd /path/to/pipeline && python run_news_tool.py    # 新增
45 15 * * 1-5 cd /path/to/pipeline && python run_daily_analysis.py
```

---

## 八、成本估算

| 项目 | 费用 | 说明 |
|------|------|------|
| AKShare | 免费 | 所有新闻接口免费 |
| pywencai | 免费 | 注意低频使用 |
| Tushare 公告 | ￥20起/年 | 200元获2000积分，够用一年 |
| DeepSeek-V3 | ￥0.5-1/天 | 按30只股票×30条新闻估算 |
| 总计 | **< ￥50/月** | |

---

## 九、风险与注意事项

1. **AKShare 接口变动**：AKShare 基于爬虫实现，上游网站改版会导致接口失效，建议锁定版本号并关注 GitHub Issues
2. **pywencai 封 IP**：高频调用会被同花顺封禁，建议每只股票间隔 3-5 秒，每日总调用 < 100 次
3. **LLM 幻觉**：LLM 可能对新闻情绪误判，建议在输出中保留原文摘要供人工复核
4. **新闻时效性**：`stock_news_em` 返回的是东方财富搜索结果，可能有数小时延迟，盘中实时性依赖 `stock_telegraph_cls`
5. **合规风险**：AKShare 和 pywencai 均声明仅供学术研究，商用需评估法律风险
