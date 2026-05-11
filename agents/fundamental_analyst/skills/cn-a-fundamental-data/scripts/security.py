from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_ERROR_LENGTH = 1000
REDACTED = "***"

_SENSITIVE_KEYS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "authorization",
    "cookie",
    "session",
)
_LONG_ALNUM_RE = re.compile(r"\b(?P<value>[A-Za-z0-9]{20,})\b")
_URL_RE = re.compile(r"(?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+)")
_KEY_PATTERN = "|".join(sorted(_SENSITIVE_KEYS, key=len, reverse=True))
_KV_EQUALS_RE = re.compile(
    rf"(?i)\b(?P<key>{_KEY_PATTERN})\b(?P<sep>\s*=\s*)(?P<value>[^\s,;&]+)"
)
_KV_COLON_RE = re.compile(
    rf"(?i)\b(?P<key>{_KEY_PATTERN})\b(?P<sep>\s*:\s*)(?P<value>[^\s,;\n\r]+)"
)


def sanitize_error(message: object) -> str:
    text = str(message)
    text = _sanitize_embedded_urls(text)
    text = _KV_EQUALS_RE.sub(r"\g<key>\g<sep>***", text)
    text = _KV_COLON_RE.sub(r"\g<key>\g<sep>***", text)
    text = _LONG_ALNUM_RE.sub(_mask_long_alnum, text)
    if len(text) > MAX_ERROR_LENGTH:
        return text[:MAX_ERROR_LENGTH]
    return text


def _mask_long_alnum(match: re.Match[str]) -> str:
    value = match.group("value")
    return f"{value[:4]}***{value[-2:]}"


def _sanitize_embedded_urls(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        try:
            split = urlsplit(raw_url)
        except ValueError:
            return raw_url

        query_pairs = parse_qsl(split.query, keep_blank_values=True)
        if not query_pairs:
            return raw_url
        replaced_pairs: list[tuple[str, str]] = []
        replaced = False
        for key, value in query_pairs:
            if key.strip().lower() in _SENSITIVE_KEYS:
                replaced_pairs.append((key, REDACTED))
                replaced = True
            else:
                replaced_pairs.append((key, value))
        if not replaced:
            return raw_url
        sanitized_query = urlencode(replaced_pairs, doseq=True)
        return urlunsplit((split.scheme, split.netloc, split.path, sanitized_query, split.fragment))

    return _URL_RE.sub(replace_url, text)
