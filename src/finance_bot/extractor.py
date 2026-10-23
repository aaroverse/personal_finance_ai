# DeepSeek API를 호출해 자연어 지출을 검증된 구조로 변환합니다.

import json
from datetime import date

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import ExpenseExtraction


class ExtractionError(RuntimeError):
    """Raised when an expense cannot be safely extracted."""


class DeepSeekExpenseExtractor:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract(self, text: str, today: date) -> ExpenseExtraction:
        if not text.strip():
            raise ExtractionError("Expense message cannot be empty.")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(today)},
                {"role": "user", "content": text.strip()},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 700,
            "temperature": 0,
        }

        for attempt in range(2):
            try:
                response = await self._client.post(
                    f"{self._settings.deepseek_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.deepseek_api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return self._validate_content(content)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                if attempt == 1:
                    raise ExtractionError("DeepSeek did not return a usable expense.") from exc

        raise ExtractionError("DeepSeek returned an empty response twice.")

    def _validate_content(self, content: str) -> ExpenseExtraction:
        try:
            raw = json.loads(content)
            result = ExpenseExtraction.model_validate(raw)
            result.is_personal = True  # Attribution is a user's explicit review choice, never an AI decision.
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionError("DeepSeek returned invalid expense fields.") from exc

        if result.ambiguities or result.confidence < 0.85 or result.payment_account is None:
            result.needs_review = True
        return result

    def _system_prompt(self, today: date) -> str:
        accounts = ", ".join(self._settings.account_names) or "none configured"
        currency = self._settings.default_currency
        return f"""You extract exactly one personal expense from a Telegram message.
Return JSON only. Never add keys outside the schema.
Today is {today.isoformat()}. Resolve relative dates using this date.
Known payment accounts: {accounts}.
Default currency is {currency}; use it only when no currency or country cue is present.
If the user says RM or Malaysia, use MYR. If the user says S$ or Singapore, use SGD.
If a field is uncertain, make the best conservative choice, set needs_review=true, and explain it in ambiguities.
If payment account is absent, use null and add an ambiguity.
Categories: dining, groceries, transport, shopping, bills, housing, health, education, travel, entertainment, cash_withdrawal, transfer, other.
Amounts must be positive and have at most two decimal places.

JSON schema example:
{{
  "transaction_date": "{today.isoformat()}",
  "amount": "12.50",
  "currency": "{currency}",
  "merchant": "merchant or useful expense label",
  "category": "dining",
  "payment_account": null,
  "note": null,
  "confidence": 0.82,
  "needs_review": true,
  "ambiguities": ["Payment account was not specified"]
}}"""
