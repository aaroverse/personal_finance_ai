# 텔레그램 명령과 지출 검토 버튼을 애플리케이션 서비스에 연결합니다.

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .database import DraftNotPendingError, DraftRecord, TransactionRecord
from .extractor import ExtractionError
from .service import FinanceService, UnauthorizedUserError


LOGGER = logging.getLogger(__name__)
EDITING_DRAFT_KEY = "editing_draft_id"


def build_application(bot_token: str, service: FinanceService) -> Application:
    application = ApplicationBuilder().token(bot_token).build()
    application.bot_data["finance_service"] = service
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("recent", recent))
    application.add_handler(CallbackQueryHandler(review_action, pattern=r"^(confirm|edit|cancel):\d+(?::[a-f0-9]{16})?$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_expense))
    application.add_error_handler(log_error)
    return application


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _reply_private(update)
        return
    await update.effective_message.reply_text(
        "你好，我会帮你记录支出。直接发一句自然语言，例如：\n"
        "今天午餐 S$12.50，用 DBS 卡。\n\n"
        "我会先整理成结构化记录，等你确认后才写入账本。"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _reply_private(update)
        return
    await update.effective_message.reply_text(
        "可用方式：\n"
        "• 直接发支出描述\n"
        "• /recent 查看最近 10 笔已确认记录\n"
        "• 审核卡片可选择确认、修改或取消\n"
        "• /whoami 查看自己的 Telegram 数字 ID"
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is not None:
        await update.effective_message.reply_text(f"你的 Telegram user ID 是：{user.id}")


async def capture_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.text is None:
        return

    service = _service(context)
    if not service.is_allowed(user.id):
        await _reply_private(update)
        return

    editing_draft_id = context.user_data.get(EDITING_DRAFT_KEY)
    try:
        await message.reply_text("正在整理这笔支出…")
        if editing_draft_id is None:
            draft = await service.submit_expense(user.id, message.text)
        else:
            draft = await service.revise_expense(user.id, int(editing_draft_id), message.text)
            context.user_data.pop(EDITING_DRAFT_KEY, None)
        await message.reply_text(_format_draft(draft), reply_markup=_review_keyboard(draft))
    except UnauthorizedUserError:
        await _reply_private(update)
    except DraftNotPendingError:
        context.user_data.pop(EDITING_DRAFT_KEY, None)
        await message.reply_text("这份草稿已经处理过了，请重新发送一笔支出。")
    except ExtractionError:
        LOGGER.warning("Expense extraction failed for allowlisted user %s", user.id)
        await message.reply_text("我暂时无法可靠地读懂这笔支出。请写清金额、币种和商户后再试一次。")


async def review_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not isinstance(query.data, str):
        return
    await query.answer()

    service = _service(context)
    if not service.is_allowed(user.id):
        await query.edit_message_text("这是私人财务机器人。你没有访问权限。")
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.edit_message_text("这张旧审核卡片已过期，请到网页查看最新草稿后审核。")
        return
    action, raw_draft_id, revision = parts
    draft_id = int(raw_draft_id)
    try:
        if action == "confirm":
            transaction = service.confirm(user.id, draft_id, revision)
            context.user_data.pop(EDITING_DRAFT_KEY, None)
            await query.edit_message_text(_format_saved(transaction))
        elif action == "edit":
            service.prepare_edit(user.id, draft_id, revision)
            context.user_data[EDITING_DRAFT_KEY] = draft_id
            await query.edit_message_text(
                "请重新发一条完整描述，我会更新这份草稿。\n"
                "例如：昨天在 FairPrice 买菜 S$42.80，用 OCBC 卡。"
            )
        else:
            service.cancel(user.id, draft_id, revision)
            context.user_data.pop(EDITING_DRAFT_KEY, None)
            await query.edit_message_text("已取消，没有写入账本。")
    except DraftNotPendingError:
        context.user_data.pop(EDITING_DRAFT_KEY, None)
        await query.edit_message_text("这份草稿已更新、已处理或不属于你。请到网页查看最新状态。")


async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    try:
        transactions = _service(context).recent(user.id)
    except UnauthorizedUserError:
        await _reply_private(update)
        return

    if not transactions:
        await message.reply_text("还没有已确认的记录。")
        return

    lines = ["最近的已确认记录："]
    for transaction in transactions:
        expense = transaction.expense
        account = expense.payment_account or "未注明账户"
        lines.append(
            f"#{transaction.id} · {expense.transaction_date.isoformat()} · "
            f"{expense.currency} {expense.amount:.2f} · {expense.merchant} · {account}"
        )
    await message.reply_text("\n".join(lines))


async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Telegram update error", exc_info=context.error)


def _service(context: ContextTypes.DEFAULT_TYPE) -> FinanceService:
    return context.application.bot_data["finance_service"]


def _is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return user is not None and _service(context).is_allowed(user.id)


async def _reply_private(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text("这是私人财务机器人。你没有访问权限。")


def _review_keyboard(draft: DraftRecord) -> InlineKeyboardMarkup:
    suffix = f"{draft.id}:{draft.revision[:16]}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 确认", callback_data=f"confirm:{suffix}"),
                InlineKeyboardButton("✏️ 修改", callback_data=f"edit:{suffix}"),
            ],
            [InlineKeyboardButton("❌ 取消", callback_data=f"cancel:{suffix}")],
        ]
    )


def _format_draft(draft: DraftRecord) -> str:
    expense = draft.expense
    account = expense.payment_account or "⚠️ 未注明"
    note = expense.note or "—"
    lines = [
        "请确认这笔记录：",
        "",
        f"日期：{expense.transaction_date.isoformat()}",
        f"金额：{expense.currency} {expense.amount:.2f}",
        f"商户：{expense.merchant}",
        f"分类：{expense.category}",
        f"账户：{account}",
        f"备注：{note}",
        f"识别信心：{expense.confidence:.0%}",
    ]
    if expense.ambiguities:
        lines.extend(["", "需要留意：", *[f"• {item}" for item in expense.ambiguities]])
    return "\n".join(lines)


def _format_saved(transaction: TransactionRecord) -> str:
    expense = transaction.expense
    return (
        "✅ 已写入账本\n\n"
        f"记录 #{transaction.id}\n"
        f"{expense.transaction_date.isoformat()} · "
        f"{expense.currency} {expense.amount:.2f} · {expense.merchant}"
    )
