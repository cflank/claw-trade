#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIR = ROOT / "src/claw_trade/data_gateway/official_catalog/generated"
COINGLASS_LLMS_URL = "https://docs.coinglass.com/llms.txt"
TUSHARE_DOCS_URL = "https://tushare.pro/document/2"
FINNHUB_DOCS_URL = "https://finnhub.io/docs/api"
FRED_DOCS_URL = "https://fred.stlouisfed.org/docs/api/fred/"
FRED_V2_DOCS_URL = "https://fred.stlouisfed.org/docs/api/fred/v2/index.html"
GLASSNODE_LLMS_URL = "https://docs.glassnode.com/llms.txt"
COINGECKO_PRO_OAS_URL = "https://raw.githubusercontent.com/coingecko/coingecko-api-oas/main/pro-api.json"
HTTP_CONNECT_TIMEOUT_SECONDS = 10
HTTP_MAX_TIME_SECONDS = 90


def main() -> int:
    parser = argparse.ArgumentParser(description="Update generated official API catalog snapshots from official docs.")
    parser.add_argument("--provider", choices=("all", "tushare", "coinglass", "coingecko_pro", "finnhub", "fred", "glassnode"), required=True)
    args = parser.parse_args()

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    providers = ("tushare", "coinglass", "coingecko_pro", "finnhub", "fred", "glassnode") if args.provider == "all" else (args.provider,)
    for provider in providers:
        if provider == "tushare":
            specs = _build_tushare_specs()
            target = GENERATED_DIR / "tushare_weborder_permission_api.json"
            target.write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {target} endpoints={len(specs)}")
            continue
        specs, filename = _build_specs(provider)
        target = GENERATED_DIR / filename
        target.write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {target} endpoints={len(specs)}")
    return 0


def _build_specs(provider: str) -> tuple[list[dict[str, Any]], str]:
    if provider == "coinglass":
        return _build_coinglass_specs(), "coinglass_v4_rest.json"
    if provider == "coingecko_pro":
        return _oas_specs(
            json.loads(_curl(COINGECKO_PRO_OAS_URL)),
            endpoint_prefix="coingecko_pro.raw_",
            doc_ref_base="https://docs.coingecko.com/reference",
            source="coingecko_pro_oas",
        ), "coingecko_pro_oas.json"
    if provider == "finnhub":
        return _oas_specs(
            _extract_finnhub_doc_schema(_curl(FINNHUB_DOCS_URL)),
            endpoint_prefix="finnhub.raw_",
            doc_ref_base=FINNHUB_DOCS_URL,
            source="finnhub_doc_schema",
        ), "finnhub_doc_schema.json"
    if provider == "fred":
        return _build_fred_specs(), "fred_api_docs.json"
    if provider == "glassnode":
        return _build_glassnode_specs(), "glassnode_basic_api.json"
    raise ValueError(f"unsupported provider: {provider}")


def _build_tushare_specs() -> list[dict[str, Any]]:
    payloads = [
        json.loads(_curl("https://tushare.pro/wctapi/goods?type=2")),
    ]
    activity_index = json.loads(_curl("https://tushare.pro/wctapi/activity_packages"))
    for item in activity_index.get("data") or ():
        activity_id = item.get("id")
        if activity_id is not None:
            payloads.append(json.loads(_curl(f"https://tushare.pro/wctapi/activity_packages/{activity_id}")))

    apis: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        _collect_tushare_apis(payload.get("data"), apis)

    specs: list[dict[str, Any]] = []
    for api_name, raw in sorted(apis.items()):
        summary = str(raw.get("api_title") or raw.get("name") or api_name)
        markets = _tushare_markets(api_name, summary)
        response_fields = _tushare_response_fields(api_name, summary)
        specs.append(
            {
                "endpoint_id": f"tushare.raw_{api_name}",
                "path": api_name,
                "method": "POST",
                "required_params": [],
                "optional_params": _tushare_optional_params(api_name),
                "parser_status": "parser_missing",
                "official_doc_ref": raw.get("api_doc_url") or TUSHARE_DOCS_URL,
                "source": "tushare_weborder_permission_api",
                "summary": summary,
                "description": str(raw.get("group_name") or raw.get("desc") or ""),
                "response_fields": response_fields,
                "markets": markets,
                "asset_classes": _tushare_asset_classes(api_name, summary),
            }
        )
    return specs


def _collect_tushare_apis(value: Any, apis: dict[str, dict[str, Any]], *, group: dict[str, Any] | None = None) -> None:
    if isinstance(value, dict):
        current_group = group
        if value.get("api_list") and value.get("name"):
            current_group = value
        api_name = str(value.get("api_name") or "").strip()
        if api_name:
            existing = apis.setdefault(api_name, dict(value))
            if current_group is not None:
                existing.setdefault("group_name", current_group.get("name"))
                existing.setdefault("desc", current_group.get("desc"))
        for child in value.values():
            _collect_tushare_apis(child, apis, group=current_group)
    elif isinstance(value, list):
        for child in value:
            _collect_tushare_apis(child, apis, group=group)


def _tushare_markets(api_name: str, summary: str) -> list[str]:
    text = f"{api_name} {summary}".lower()
    if api_name.startswith(("hk_", "rt_hk_")) or "港股" in summary:
        return ["HK"]
    if api_name.startswith(("us_", "rt_us_")) or "美股" in summary:
        return ["US"]
    if api_name.startswith(("ft_", "rt_fut_", "opt_")) or "期货" in summary or "期权" in summary:
        return ["CN_A"]
    return ["CN_A"]


def _tushare_optional_params(api_name: str) -> list[str]:
    params = ["ts_code", "start_date", "end_date", "trade_date", "fields"]
    if api_name in {"news", "major_news", "cctv_news", "npr", "research_report", "monetary_policy", "yc_cb"}:
        params.extend(["start_time", "end_time", "src", "limit"])
    if api_name.startswith("rt_"):
        params.extend(["freq", "src"])
    if api_name.endswith("_mins") or api_name in {"stk_mins", "hk_mins", "idx_mins", "sw_mins", "etf_mins"}:
        params.extend(["freq", "limit"])
    return sorted(dict.fromkeys(params))


def _tushare_response_fields(api_name: str, summary: str) -> list[str]:
    tokens = {api_name.replace("_", " "), api_name}
    if any(key in api_name for key in ("rt_", "_mins", "min", "daily", "_k")) or any(key in summary for key in ("行情", "日线", "分钟", "实时")):
        tokens.update({"daily_bar", "intraday_bar", "quote_snapshot", "realtime", "open", "high", "low", "close", "price", "volume"})
    if any(key in api_name for key in ("news", "report", "npr", "monetary")) or any(key in summary for key in ("新闻", "研报", "政策", "报告")):
        tokens.update({"company_news", "macro_news", "research_report", "policy", "title", "published_at", "summary"})
    if any(key in api_name for key in ("anns", "qa", "irm")) or any(key in summary for key in ("公告", "互动", "问答")):
        tokens.update({"official_filing", "social_signal", "announcement", "question", "answer"})
    if any(key in api_name for key in ("auction", "premarket")) or any(key in summary for key in ("集合竞价", "盘前")):
        tokens.update({"auction", "order_book_snapshot", "bid", "ask", "volume", "price"})
    if any(key in api_name for key in ("hot", "limit", "concept", "index", "member", "hm_", "top_")) or any(
        key in summary for key in ("龙虎榜", "涨跌停", "板块", "游资", "热门", "题材")
    ):
        tokens.update({"hot_money_event", "sector_snapshot", "social_signal", "capital_flow"})
    if "yc_cb" in api_name or "收益率" in summary:
        tokens.update({"macro", "bond_yield", "interest_rate"})
    return sorted(tokens)


def _tushare_asset_classes(api_name: str, summary: str) -> list[str]:
    text = f"{api_name} {summary}"
    if api_name.startswith(("hk_", "rt_hk_")) or "港股" in summary:
        return ["hk_equity"]
    if api_name.startswith("us_") or "美股" in summary:
        return ["us_equity"]
    if api_name.startswith(("ft_", "rt_fut_")) or "期货" in summary:
        return ["future"]
    if api_name.startswith("opt_") or "期权" in summary:
        return ["option"]
    if api_name.startswith(("etf_", "rt_etf_")) or "ETF" in text.upper():
        return ["etf"]
    if api_name.startswith(("idx_", "rt_idx_", "sw_", "rt_sw_")) or "指数" in summary or "申万" in summary:
        return ["index"]
    if any(key in api_name for key in ("concept", "index", "member", "daily")) and any(
        key in api_name for key in ("dc_", "tdx_", "ths_", "sw_")
    ):
        return ["sector"]
    return ["cn_a_equity"]


def _build_coinglass_specs() -> list[dict[str, Any]]:
    index = _curl(COINGLASS_LLMS_URL)
    pages = _coinglass_pages(index)
    specs: list[dict[str, Any]] = []
    for page in pages:
        markdown = _curl(page["url"])
        openapi = _extract_openapi(markdown)
        if not openapi:
            continue
        for path, methods in sorted(openapi.get("paths", {}).items()):
            if not isinstance(methods, dict):
                continue
            for method, operation in sorted(methods.items()):
                if method.lower() not in {"get", "post"} or not isinstance(operation, dict):
                    continue
                required, optional = _params(operation)
                specs.append(
                    {
                        "endpoint_id": f"coinglass.raw_{_slug_from_path(path)}",
                        "path": path,
                        "method": method.upper(),
                        "required_params": required,
                        "optional_params": optional,
                        "parser_status": "parser_missing",
                        "official_doc_ref": page["url"],
                        "source": "coinglass_llms_openapi",
                        "summary": page["title"],
                        "description": _first_sentence(markdown),
                        "response_fields": _response_fields(markdown),
                    }
                )
    return _dedupe_specs(specs)


def _extract_finnhub_doc_schema(page: str) -> dict[str, Any]:
    marker = "window.docSchema = "
    marker_index = page.find(marker)
    if marker_index < 0:
        raise RuntimeError("finnhub docSchema not found")
    start = page.find("{", marker_index)
    if start < 0:
        raise RuntimeError("finnhub docSchema JSON start not found")
    return json.loads(page[start:_matching_json_object_end(page, start)])


def _build_glassnode_specs() -> list[dict[str, Any]]:
    index = _curl(GLASSNODE_LLMS_URL)
    pages = [
        url
        for _, url in re.findall(r"^- \[([^\]]+)\]\((https://docs\.glassnode\.com/basic-api/endpoints/[^)]+\.md)\)", index, re.M)
    ]
    specs: list[dict[str, Any]] = []
    failed_pages: list[str] = []
    for page in pages:
        markdown = _curl_optional(page)
        if markdown is None:
            failed_pages.append(page)
            continue
        for openapi in _json_line_openapi_specs(markdown):
            specs.extend(
                _oas_specs(
                    openapi,
                    endpoint_prefix="glassnode.raw_",
                    doc_ref_base=page,
                    source="glassnode_llms_openapi",
                    doc_ref_override=page,
                )
            )
    if failed_pages:
        print(f"warning: glassnode pages failed: {', '.join(failed_pages)}", file=sys.stderr)
    return _dedupe_specs(specs)


def _build_fred_specs() -> list[dict[str, Any]]:
    refs = _fred_refs(FRED_DOCS_URL, _curl(FRED_DOCS_URL))
    refs.extend(_fred_refs(FRED_V2_DOCS_URL, _curl(FRED_V2_DOCS_URL)))
    specs: list[dict[str, Any]] = []
    for base_url, href, api_path in refs:
        doc_ref = urljoin(base_url, href)
        page = _curl(doc_ref)
        path = f"/{html.unescape(api_path)}"
        params = _fred_params(page)
        required = _fred_required_params(page)
        optional = sorted((params - required) | {"file_type"})
        specs.append(
            {
                "endpoint_id": f"fred.raw_{_slug_from_path(path)}",
                "path": path,
                "method": "GET",
                "required_params": sorted(required),
                "optional_params": optional,
                "parser_status": "parser_missing",
                "official_doc_ref": doc_ref,
                "source": "fred_api_docs",
                "summary": _html_title(page),
                "description": _first_html_paragraph(page),
                "response_fields": _response_fields(page),
            }
        )
    return _dedupe_specs(specs)


def _fred_refs(base_url: str, index: str) -> list[tuple[str, str, str]]:
    refs = []
    for href, api_path in re.findall(r'<a href="([^"]+\.html)" name="[^"]+">(fred(?:/v2)?/[^<]+)</a>', index):
        refs.append((base_url, href, api_path))
    return refs


def _oas_specs(
    openapi: dict[str, Any],
    *,
    endpoint_prefix: str,
    doc_ref_base: str,
    source: str,
    doc_ref_override: str | None = None,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for path, methods in sorted(openapi.get("paths", {}).items()):
        if not isinstance(methods, dict):
            continue
        for method, operation in sorted(methods.items()):
            if method.lower() not in {"get", "post"} or not isinstance(operation, dict):
                continue
            required, optional = _params(operation)
            specs.append(
                {
                    "endpoint_id": f"{endpoint_prefix}{_slug_from_path(path)}",
                    "path": path,
                    "method": method.upper(),
                    "required_params": _without_auth_params(required),
                    "optional_params": _without_auth_params(optional),
                    "parser_status": "parser_missing",
                    "official_doc_ref": doc_ref_override or _endpoint_doc_ref(doc_ref_base, operation, path),
                    "source": source,
                    "summary": str(operation.get("summary") or operation.get("title") or ""),
                    "description": _strip_markup(str(operation.get("description") or "")),
                    "response_fields": _schema_fields(openapi=openapi, operation=operation),
                }
            )
    return _dedupe_specs(specs)


def _coinglass_pages(index: str) -> list[dict[str, str]]:
    pages: list[dict[str, str]] = []
    for title, url in re.findall(r"^- \[([^\]]+)\]\((https://docs\.coinglass\.com/reference/[^)]+\.md)\)", index, re.M):
        slug = url.rsplit("/", 1)[-1].lower()
        if slug.startswith(("websocket_", "ws-")) or slug in {
            "authentication.md",
            "change-log.md",
            "coinglass-agent-skill.md",
            "endpoint-overview.md",
            "enterprise-customization.md",
            "getting-started-with-your-api.md",
            "mcp-service.md",
            "responses-error-codes.md",
        }:
            continue
        pages.append({"title": title.strip(), "url": url.strip()})
    return pages


def _extract_openapi(markdown: str) -> dict[str, Any] | None:
    if "# OpenAPI definition" not in markdown:
        return None
    tail = markdown.split("# OpenAPI definition", 1)[1]
    match = re.search(r"```json\s*(\{.*?\})\s*```", tail, re.S)
    if not match:
        return None
    return json.loads(match.group(1))


def _params(operation: dict[str, Any]) -> tuple[list[str], list[str]]:
    required: list[str] = []
    optional: list[str] = []
    for raw in operation.get("parameters", ()):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        if raw.get("required") is True:
            required.append(name)
        else:
            optional.append(name)
    return sorted(set(required)), sorted(set(optional))


def _without_auth_params(params: list[str]) -> list[str]:
    return [param for param in params if param not in {"api_key", "apikey", "token"}]


def _response_fields(markdown: str) -> list[str]:
    before_openapi = markdown.split("# OpenAPI definition", 1)[0]
    fields: set[str] = set()
    for block in re.findall(r"```(?:json)?\s*(.*?)\s*```", before_openapi, re.S):
        fields.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', block))
    return sorted(fields)


def _schema_fields(*, openapi: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    fields: set[str] = set()
    for response in (operation.get("responses") or {}).values():
        if not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        for media in content.values() if isinstance(content, dict) else ():
            if isinstance(media, dict):
                fields.update(_fields_from_schema(openapi, media.get("schema")))
        schema = response.get("schema")
        fields.update(_fields_from_schema(openapi, schema))
    sample = operation.get("sampleResponse")
    if isinstance(sample, str):
        fields.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', sample))
    return sorted(fields)


def _fields_from_schema(openapi: dict[str, Any], schema: Any, *, depth: int = 0) -> set[str]:
    if depth > 4 or not isinstance(schema, dict):
        return set()
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _fields_from_schema(openapi, _resolve_ref(openapi, ref), depth=depth + 1)
    fields = {str(key) for key in (schema.get("properties") or {}) if str(key).strip()}
    if isinstance(schema.get("items"), dict):
        fields.update(_fields_from_schema(openapi, schema["items"], depth=depth + 1))
    combined_schemas = [*(schema.get("oneOf") or ()), *(schema.get("anyOf") or ()), *(schema.get("allOf") or ())]
    for nested in combined_schemas:
        fields.update(_fields_from_schema(openapi, nested, depth=depth + 1))
    for value in (schema.get("properties") or {}).values():
        fields.update(_fields_from_schema(openapi, value, depth=depth + 1))
    return fields


def _resolve_ref(openapi: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return {}
    current: Any = openapi
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            return {}
        current = current.get(part)
    return current or {}


def _json_line_openapi_specs(markdown: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for match in re.finditer(r'^\{.*"openapi".*\}$', markdown, re.M):
        specs.append(json.loads(match.group(0)))
    return specs


def _fred_params(page: str) -> set[str]:
    params = set(re.findall(r'<a href="#([A-Za-z_][A-Za-z0-9_]*)">', page))
    params.update(re.findall(r"[?&amp;]([A-Za-z_][A-Za-z0-9_]*)=", page))
    return {param for param in params if param not in {"api_key"}}


def _fred_required_params(page: str) -> set[str]:
    request = re.search(r"<pre>https://api\.stlouisfed\.org/[^?]+[?]([^<]+)</pre>", page)
    if not request:
        return set()
    query = html.unescape(request.group(1))
    return {name for name, value in parse_qsl(query, keep_blank_values=True) if name not in {"api_key", "file_type"} and value}


def _html_title(page: str) -> str:
    match = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    return _strip_markup(match.group(1)) if match else ""


def _first_html_paragraph(page: str) -> str:
    for match in re.finditer(r"<p[^>]*>(.*?)</p>", page, re.S | re.I):
        text = _strip_markup(match.group(1))
        if len(text) > 20:
            return text[:500]
    return ""


def _endpoint_doc_ref(base: str, operation: dict[str, Any], path: str) -> str:
    url_id = operation.get("urlId")
    if isinstance(url_id, str) and url_id.strip():
        return f"{base.rstrip('/')}/{url_id.strip()}"
    return f"{base.rstrip('/')}#{_slug_from_path(path)}"


def _matching_json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise RuntimeError("JSON object end not found")


def _first_sentence(markdown: str) -> str:
    lines = [line.strip() for line in markdown.splitlines() if line.strip() and not line.startswith((">", "#", "*", "|", "```"))]
    for line in lines:
        if len(line) > 20:
            return line[:500]
    return ""


def _slug_from_path(path: str) -> str:
    value = path.strip("/").replace("api/", "")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value or "root"


def _strip_markup(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _dedupe_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in specs:
        by_key[(spec["method"], spec["path"])] = spec
    return [by_key[key] for key in sorted(by_key)]


def _curl(url: str) -> str:
    result = subprocess.run(
        [
            "curl",
            "--connect-timeout",
            str(HTTP_CONNECT_TIMEOUT_SECONDS),
            "--max-time",
            str(HTTP_MAX_TIME_SECONDS),
            "-fsSL",
            url,
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def _curl_optional(url: str) -> str | None:
    try:
        return _curl(url)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"warning: failed to fetch {url}: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
