# 환경 변수에서 안전하게 애플리케이션 설정을 불러옵니다.

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: str
    deepseek_api_key: SecretStr
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    default_currency: str = "SGD"
    app_timezone: str = "Asia/Singapore"
    known_accounts: str = "MY UOB,MY Maybank,SG Citibank,SG UOB,SG DBS,SG OCBC,Cash"
    database_path: Path = Path("data/finance.db")
    log_level: str = "INFO"

    @field_validator("telegram_bot_token", "deepseek_api_key")
    @classmethod
    def reject_placeholder_secrets(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw or raw.startswith("replace_with_"):
            raise ValueError("must be set to a real value in .env")
        return SecretStr(raw)

    @field_validator("default_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("must be a three-letter currency code")
        return normalized

    @field_validator("telegram_allowed_user_ids")
    @classmethod
    def validate_allowed_user_ids(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("must contain at least one Telegram user ID")
        try:
            parsed = [int(item) for item in items]
        except ValueError as exc:
            raise ValueError("must contain comma-separated integers") from exc
        if any(user_id < 0 for user_id in parsed):
            raise ValueError("must contain non-negative Telegram user IDs")
        return ",".join(str(user_id) for user_id in parsed)

    @field_validator("deepseek_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("must use HTTPS")
        return normalized

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(int(item) for item in self.telegram_allowed_user_ids.split(","))

    @property
    def account_names(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.known_accounts.split(",") if item.strip())
