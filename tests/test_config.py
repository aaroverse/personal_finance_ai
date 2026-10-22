# 환경 설정과 사용자 허용 목록 검증을 테스트합니다.

import pytest
from pydantic import ValidationError

from finance_bot.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:valid-test-token",
        "telegram_allowed_user_ids": "42, 84",
        "deepseek_api_key": "sk-valid-test-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_parses_allowlist_and_accounts() -> None:
    settings = make_settings(known_accounts="SG DBS, MY Maybank, Cash")

    assert settings.allowed_user_ids == frozenset({42, 84})
    assert settings.account_names == ("SG DBS", "MY Maybank", "Cash")


def test_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError):
        make_settings(deepseek_api_key="replace_with_deepseek_api_key")


def test_rejects_non_https_api_url() -> None:
    with pytest.raises(ValidationError):
        make_settings(deepseek_base_url="http://api.deepseek.com")


def test_rejects_invalid_allowlist_during_startup() -> None:
    with pytest.raises(ValidationError, match="comma-separated integers"):
        make_settings(telegram_allowed_user_ids="not-a-number")
