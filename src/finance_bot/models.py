# 추출된 지출과 저장된 초안의 검증 모델을 정의합니다.

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ExpenseCategory = Literal[
    "dining",
    "groceries",
    "transport",
    "shopping",
    "bills",
    "housing",
    "health",
    "education",
    "travel",
    "entertainment",
    "cash_withdrawal",
    "transfer",
    "other",
]


class ExpenseExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transaction_date: date
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    merchant: str = Field(min_length=1, max_length=120)
    category: ExpenseCategory
    is_personal: bool = Field(default=True, strict=True)
    payment_account: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=300)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    ambiguities: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must contain letters only")
        return normalized

    @field_validator("merchant")
    @classmethod
    def reject_unknown_merchant(cls, value: str) -> str:
        if value.lower() in {"unknown", "n/a", "null"}:
            raise ValueError("merchant must be a useful label")
        return value

    @field_validator("ambiguities")
    @classmethod
    def clean_ambiguities(cls, values: list[str]) -> list[str]:
        return [item.strip()[:160] for item in values if item.strip()]
