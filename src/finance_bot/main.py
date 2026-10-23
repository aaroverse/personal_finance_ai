# 설정과 저장소를 초기화하고 텔레그램 봇을 실행합니다.

import asyncio
import logging

from telegram import Update

from .bot import build_application
from .config import Settings
from .database import FinanceRepository
from .extractor import DeepSeekExpenseExtractor
from .service import FinanceService


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=settings.log_level.upper(),
    )
    # HTTP request URLs contain the Telegram token; do not emit routine network logs.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    repository = FinanceRepository(settings.database_path)
    repository.initialize()
    extractor = DeepSeekExpenseExtractor(settings)
    service = FinanceService(settings, repository, extractor)
    application = build_application(settings.telegram_bot_token.get_secret_value(), service)
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    finally:
        asyncio.run(extractor.close())


if __name__ == "__main__":
    main()
