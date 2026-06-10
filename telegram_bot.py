"""
Telegram bot interface for the Training Coach Agent.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Add TELEGRAM_BOT_TOKEN=<token> to your .env file
  3. Optionally add TELEGRAM_ALLOWED_USER_IDS=123456789 to restrict access
     (get your id by messaging @userinfobot)
  4. Run: python telegram_bot.py

The bot maintains separate conversation history per user.
"""

import os
import logging
import asyncio
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from chat_agent import run_agent
from coach_tools import memory as db_memory

load_dotenv(override=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Access control ---
_raw_allowed = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = (
    {int(uid.strip()) for uid in _raw_allowed.split(",") if uid.strip()}
    if _raw_allowed
    else set()  # empty = allow anyone (useful during initial setup)
)

# Per-user conversation history: {user_id: [HumanMessage, AIMessage, ...]}
_conversations: dict[int, list] = {}

MAX_HISTORY_TURNS = 10  # keep last N human+AI pairs to limit token usage


def _is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def _trim_history(history: list) -> list:
    """Keep only the last MAX_HISTORY_TURNS human+AI pairs."""
    pairs = []
    for msg in history:
        if isinstance(msg, (HumanMessage, AIMessage)):
            pairs.append(msg)
    return pairs[-(MAX_HISTORY_TURNS * 2):]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    _conversations[user.id] = []
    await update.message.reply_text(
        f"👋 Hola {user.first_name}! Soy tu coach de entrenamiento.\n\n"
        "Puedes preguntarme sobre tus actividades, planes, estado de forma, etc.\n"
        "Usa /reset para limpiar el historial de conversación."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    _conversations[user.id] = []
    await update.message.reply_text("🔄 Historial borrado. ¡Empezamos de nuevo!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    # Show "typing..." indicator while the agent thinks
    await update.message.chat.send_action(ChatAction.TYPING)

    history = _conversations.get(user.id, [])
    history.append(HumanMessage(content=user_text))

    try:
        # run_agent is synchronous — run in a thread to avoid blocking the event loop
        reply = await asyncio.get_event_loop().run_in_executor(
            None, run_agent, history
        )
    except Exception as e:
        logger.error("Agent error for user %s: %s", user.id, e)
        reply = "⚠️ Ocurrió un error al procesar tu mensaje. Inténtalo de nuevo."

    history.append(AIMessage(content=reply))
    _conversations[user.id] = _trim_history(history)

    # Telegram has a 4096 char limit per message; split if needed
    for chunk in _split_message(reply):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split long messages into chunks that fit Telegram's limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN not set. Add it to your .env file.\n"
            "Get a token from @BotFather on Telegram."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Press Ctrl+C to stop.")
    if ALLOWED_USER_IDS:
        logger.info("Access restricted to user IDs: %s", ALLOWED_USER_IDS)
    else:
        logger.warning("No TELEGRAM_ALLOWED_USER_IDS set — anyone can use this bot!")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        db_memory.close()
