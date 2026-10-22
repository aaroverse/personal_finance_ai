# DeepSeek JSON 요청과 로컬 구조 검증을 네트워크 없이 테스트합니다.

import json
from datetime import date

import httpx
import pytest

from finance_bot.config import Settings
from finance_bot.extractor import DeepSeekExpenseExtractor, ExtractionError


def settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123456:valid-test-token",
        telegram_allowed_user_ids="42",
        deepseek_api_key="sk-valid-test-key",
    )


@pytest.mark.asyncio
async def test_requests_json_mode_and_validates_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert "Return JSON only" in payload["messages"][0]["content"]
        assert request.headers["authorization"] == "Bearer sk-valid-test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "transaction_date": "2026-10-22",
                                    "amount": "12.50",
                                    "currency": "SGD",
                                    "merchant": "Kopitiam",
                                    "category": "dining",
                                    "payment_account": None,
                                    "note": None,
                                    "confidence": 0.9,
                                    "needs_review": False,
                                    "ambiguities": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExpenseExtractor(settings(), client)
        result = await extractor.extract("午餐 12.50", date(2026, 10, 22))

    assert result.merchant == "Kopitiam"
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_retries_empty_response_once() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExpenseExtractor(settings(), client)
        with pytest.raises(ExtractionError, match="empty response twice"):
            await extractor.extract("lunch", date(2026, 10, 22))

    assert attempts == 2
