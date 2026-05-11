from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_ERROR_LENGTH = 512
TRUNCATION_SUFFIX = "[truncated]"
INVALID_URL_GAP = "invalid_url"

_SENSITIVE_KEYS = {
    "token",
    "api_key",
    "authorization",
    "cookie",
    "passwd",
    "secret",
    "key",
    "password",
    "bearer",
}
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"(?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+)")

_SENSITIVE_KEY_PATTERN = "|".join(sorted(_SENSITIVE_KEYS, key=len, reverse=True))
_KV_EQUALS_RE = re.compile(
    rf"(?i)\b(?P<key>{_SENSITIVE_KEY_PATTERN})\b(?P<sep>\s*=\s*)(?P<value>[^\s,;&]+)"
)
_KV_COLON_RE = re.compile(
    rf"(?i)\b(?P<key>{_SENSITIVE_KEY_PATTERN})\b(?P<sep>\s*:\s*)(?P<value>[^,;\n\r]+)"
)
_KV_QUOTED_RE = re.compile(
    rf'(?i)(?P<prefix>"?(?:{_SENSITIVE_KEY_PATTERN})"?\s*:\s*")(?P<value>[^"]*)(?P<suffix>")'
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+(?P<token>[^\s,;]+)")


@dataclass(frozen=True)
class UrlValidationResult:
    sanitized_url: str | None
    valid: bool
    evidence_gap: str | None


def _sanitize_query(query: str) -> str:
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return query
    sanitized_pairs = []
    for key, value in pairs:
        if key.lower() in _SENSITIVE_KEYS:
            sanitized_pairs.append((key, "***"))
        else:
            sanitized_pairs.append((key, value))
    return urlencode(sanitized_pairs, doseq=True)


def _sanitize_embedded_urls(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        try:
            split = urlsplit(raw_url)
        except ValueError:
            return raw_url
        if not split.query:
            return raw_url
        sanitized_query = _sanitize_query(split.query)
        if sanitized_query == split.query:
            return raw_url
        return urlunsplit(
            (split.scheme, split.netloc, split.path, sanitized_query, split.fragment)
        )

    return _URL_RE.sub(_replace, text)


def sanitize_error(value: object) -> str:
    text = str(value)
    text = _sanitize_embedded_urls(text)
    text = _KV_EQUALS_RE.sub(r"\g<key>\g<sep>***", text)
    text = _KV_COLON_RE.sub(r"\g<key>\g<sep>***", text)
    text = _KV_QUOTED_RE.sub(r"\g<prefix>***\g<suffix>", text)
    text = _BEARER_TOKEN_RE.sub("Bearer ***", text)
    if len(text) <= MAX_ERROR_LENGTH:
        return text
    return f"{text[:MAX_ERROR_LENGTH]}{TRUNCATION_SUFFIX}"


def validate_external_url(url: str | None) -> UrlValidationResult:
    if url is None:
        return UrlValidationResult(sanitized_url=None, valid=False, evidence_gap=None)

    candidate = url.strip()
    if not candidate:
        return UrlValidationResult(sanitized_url=None, valid=False, evidence_gap=None)
    if _CONTROL_CHARS_RE.search(candidate):
        return UrlValidationResult(
            sanitized_url=None, valid=False, evidence_gap=INVALID_URL_GAP
        )

    try:
        split = urlsplit(candidate)
    except ValueError:
        return UrlValidationResult(
            sanitized_url=None, valid=False, evidence_gap=INVALID_URL_GAP
        )

    if split.scheme.lower() not in {"http", "https"} or not split.netloc:
        return UrlValidationResult(
            sanitized_url=None, valid=False, evidence_gap=INVALID_URL_GAP
        )

    sanitized_query = _sanitize_query(split.query)
    sanitized_url = urlunsplit(
        (split.scheme.lower(), split.netloc, split.path, sanitized_query, split.fragment)
    )
    return UrlValidationResult(sanitized_url=sanitized_url, valid=True, evidence_gap=None)
