from __future__ import annotations

import pytest

from claw_trade.providers.tushare_client import TushareClientConfigError, create_tushare_pro


class _FakePro:
    pass


class _FakeTushareModule:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.pro = _FakePro()

    def pro_api(self, token: str) -> _FakePro:
        self.tokens.append(token)
        return self.pro


def test_create_tushare_pro_does_not_set_private_http_url_when_not_configured() -> None:
    module = _FakeTushareModule()

    pro = create_tushare_pro(token="token-a", env={}, tushare_module=module)

    assert pro is module.pro
    assert module.tokens == ["token-a"]
    assert not hasattr(pro, "_DataApi__http_url")


def test_create_tushare_pro_reads_token_and_url_from_env() -> None:
    module = _FakeTushareModule()

    pro = create_tushare_pro(
        env={"TUSHARE_TOKEN": " token-b ", "TUSHARE_HTTP_URL": "http://example.internal/"},
        tushare_module=module,
    )

    assert module.tokens == ["token-b"]
    assert getattr(pro, "_DataApi__http_url") == "http://example.internal/"


def test_create_tushare_pro_reads_cn_a_tushare_http_url_from_env() -> None:
    module = _FakeTushareModule()

    pro = create_tushare_pro(
        env={"TUSHARE_TOKEN": "token-c", "CN_A_TUSHARE_HTTP_URL": "https://cn-a.example.internal/"},
        tushare_module=module,
    )

    assert module.tokens == ["token-c"]
    assert getattr(pro, "_DataApi__http_url") == "https://cn-a.example.internal/"


def test_create_tushare_pro_prefers_explicit_http_url_over_env_url() -> None:
    module = _FakeTushareModule()

    pro = create_tushare_pro(
        token="token-f",
        http_url="https://arg.example/",
        env={"TUSHARE_HTTP_URL": "https://env.example/"},
        tushare_module=module,
    )

    assert module.tokens == ["token-f"]
    assert getattr(pro, "_DataApi__http_url") == "https://arg.example/"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TUSHARE_TOKEN": "token-d", "TUSHARE_HTTP_URL": "   "}, "TUSHARE_HTTP_URL must not be empty"),
        (
            {"TUSHARE_TOKEN": "token-e", "CN_A_TUSHARE_HTTP_URL": "ftp://invalid"},
            "CN_A_TUSHARE_HTTP_URL must start with http:// or https://",
        ),
    ],
)
def test_create_tushare_pro_rejects_invalid_http_url_configuration(
    env: dict[str, str],
    expected: str,
) -> None:
    with pytest.raises(TushareClientConfigError) as exc_info:
        create_tushare_pro(env=env, tushare_module=_FakeTushareModule())

    assert str(exc_info.value) == expected


def test_create_tushare_pro_rejects_missing_token() -> None:
    with pytest.raises(TushareClientConfigError) as exc_info:
        create_tushare_pro(env={}, tushare_module=_FakeTushareModule())

    assert str(exc_info.value) == "TUSHARE_TOKEN missing"
