"""Telegram polling adapter for the existing AI cashier logic.

Run with: python -m app.telegram_bot
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot import build_default_bot

log = logging.getLogger(__name__)
TELEGRAM_MAX_MESSAGE_LENGTH = 4000


def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split long answers into Telegram-safe chunks, preferring line boundaries."""
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks or ["صار عندي خلل بسيط 🙏 ممكن تعيد رسالتك؟"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "أهلاً وسهلاً! 👋\nأنا كاشير المطعم. ابعث اسم الطلب أو اكتب «المنيو» لعرض الأصناف."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not message.text:
        return

    cashier = context.application.bot_data["cashier"]
    # Run synchronous LLM/order logic off the asyncio event loop.
    answer = await asyncio.to_thread(cashier.reply, f"telegram:{chat.id}", message.text.strip())
    for part in split_message(answer):
        await message.reply_text(part)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("حالياً بقدر أتعامل مع الرسائل النصية. ابعث طلبك كتابةً 🙏")


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "put_your_telegram_bot_token_here":
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in your .env before starting the Telegram bot")

    application = Application.builder().token(token).build()
    application.bot_data["cashier"] = build_default_bot()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.TEXT, unsupported))
    log.info("Telegram cashier is starting with long polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
