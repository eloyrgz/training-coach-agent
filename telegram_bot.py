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
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from chat_agent import run_agent
from activity_notifications import build_activity_keyboard, build_note_keyboard, build_rpe_keyboard
from injury_agent import run_injury_assessment, close_db_memory as close_injury_memory
from coach_tools import memory as db_memory, save_activity_note, save_activity_rpe
from sync_pipeline import build_pipeline_from_env

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


def _is_notification_chat_allowed(user_id: int, chat_id: int) -> bool:
    return bool(ALLOWED_USER_IDS) and user_id in ALLOWED_USER_IDS and chat_id == user_id


def _trim_history(history: list) -> list:
    """Keep only the last MAX_HISTORY_TURNS human+AI pairs."""
    pairs = []
    for message in history:
        if isinstance(message, (HumanMessage, AIMessage)):
            pairs.append(message)
    return pairs[-(MAX_HISTORY_TURNS * 2):]


async def handle_note_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    message = query.message
    user_id = query.from_user.id
    chat_id = message.chat_id if message else None
    if chat_id is None or not _is_notification_chat_allowed(user_id, chat_id):
        await query.answer("⛔ No tienes acceso a esta acción.", show_alert=True)
        return

    parts = (query.data or "").split(":", 1)
    if len(parts) != 2 or parts[0] != "note" or not parts[1]:
        await query.answer("Acción no válida.", show_alert=True)
        return

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    pending_saved = await asyncio.get_event_loop().run_in_executor(
        None, db_memory.set_pending_input, chat_id, parts[1], expires_at
    )
    if not pending_saved:
        await query.answer("No se pudo preparar la nota. Inténtalo de nuevo.", show_alert=True)
        return

    await query.answer()
    await message.reply_text("Escribe la nota para este entrenamiento. Tienes 30 minutos.")


async def handle_rpe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    message = query.message
    user_id = query.from_user.id
    chat_id = message.chat_id if message else None
    if chat_id is None or not _is_notification_chat_allowed(user_id, chat_id):
        await query.answer("⛔ No tienes acceso a esta acción.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "rpe" or not parts[1]:
        await query.answer("Acción no válida.", show_alert=True)
        return
    try:
        rpe = int(parts[2])
    except ValueError:
        await query.answer("RPE no válido.", show_alert=True)
        return
    if not 1 <= rpe <= 10:
        await query.answer("RPE no válido.", show_alert=True)
        return

    result = await asyncio.get_event_loop().run_in_executor(
        None, save_activity_rpe, parts[1], rpe
    )
    if result.get("error") or not result.get("local_db_updated"):
        await query.answer(result.get("error", "No se pudo guardar el RPE."), show_alert=True)
        return

    await query.answer()
    confirmation = f"RPE {rpe} guardado."
    if not result.get("intervals_icu_updated"):
        confirmation += " No se pudo sincronizar con Intervals.icu."
    original_text = message.text or message.caption or ""
    await query.edit_message_text(
        f"{original_text}\n\n{confirmation}".strip(),
        reply_markup=build_note_keyboard(parts[1]),
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    _conversations[user.id] = []
    await update.message.reply_text(
        f"👋 Hola {user.first_name}! Soy tu coach de entrenamiento.\n\n"
        "Puedes preguntarme sobre tus actividades, planes, estado de forma, etc.\n"
        "• /medhist set <texto> — guarda historial médico/de lesiones\n"
        "• /medhist show — muestra historial guardado\n"
        "• /medhist clear — borra historial guardado\n"
        "• /injury <síntomas> — evaluación rápida de riesgo de lesión\n"
        "• /sync — sincroniza tus últimas actividades desde Intervals.icu\n"
        "• /sync 7 — sincroniza los últimos N días\n"
        "• /reset — borra el historial de conversación"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        return
    _conversations[user.id] = []
    await update.message.reply_text("🔄 Historial borrado. ¡Empezamos de nuevo!")


async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    days = 1
    if context.args:
        try:
            days = int(context.args[0])
            if days < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Uso: /sync [días] (e.g. /sync 7)")
            return

    await update.message.reply_text(f"🔄 Sincronizando últimos {days} día(s)... espera un momento.")
    await update.message.chat.send_action(ChatAction.TYPING)

    def _run_sync():
        pipeline = build_pipeline_from_env()
        pipeline.sync_activities(days_back=days)
        pipeline.close()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run_sync)
        await update.message.reply_text(
            "✅ Sincronización completada. ¡Ya puedes preguntarme sobre tu actividad!"
        )
    except Exception as e:
        logger.error("Sync error for user %s: %s", user.id, e)
        await update.message.reply_text(f"⚠️ Error durante la sincronización: {e}")


async def injury(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    user_text = " ".join(context.args).strip() if context.args else ""
    if not user_text:
        await update.message.reply_text(
            "⚠️ Uso: /injury <describe síntomas + contexto>.\n"
            "Ejemplo: /injury Dolor 6/10 en antepié, inflamación, peor al correr más de 30 min."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, run_injury_assessment, user_text
        )
    except Exception as e:
        logger.error("Injury agent error for user %s: %s", user.id, e)
        await update.message.reply_text(
            "⚠️ Ocurrió un error al evaluar el riesgo de lesión. Inténtalo de nuevo."
        )
        return

    risk = result.get("evaluation_risk", "N/A")
    reasons = result.get("risk_reasons") or []
    reasons_line = "\n".join([f"- {r}" for r in reasons]) if reasons else "- Sin razones detectadas"
    prescription = result.get("final_prescription", "Sin recomendación disponible.")

    response = (
        f"🩺 *Evaluación de lesión*\n"
        f"*Riesgo:* {risk}\n"
        f"*Motivos:*\n{reasons_line}\n\n"
        f"{prescription}"
    )

    for chunk in _split_message(response):
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def medhist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Uso: /medhist set <texto> | /medhist show | /medhist clear"
        )
        return

    action = context.args[0].lower().strip()

    if action == "show":
        entries = db_memory.get_medical_background()
        if not entries:
            await update.message.reply_text("ℹ️ No hay historial médico guardado.")
            return
        lines = "\n".join([f"- {e['text']}" for e in entries])
        await update.message.reply_text(f"🧾 Historial médico guardado:\n{lines}")
        return

    if action == "clear":
        db_memory.clear_medical_background()
        await update.message.reply_text("🗑️ Historial médico borrado.")
        return

    if action == "set":
        history_text = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""
        if not history_text:
            await update.message.reply_text(
                "⚠️ Uso: /medhist set <texto con lesiones previas, cirugías y condiciones relevantes>"
            )
            return
        db_memory.add_medical_background(history_text)
        await update.message.reply_text("✅ Historial médico guardado en base de datos. Se usará automáticamente en /injury.")
        return

    await update.message.reply_text(
        "⚠️ Acción no válida. Usa: /medhist set <texto> | /medhist show | /medhist clear"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ No tienes acceso a este bot.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    pending = await asyncio.get_event_loop().run_in_executor(
        None, db_memory.get_pending_input, chat_id
    )
    if pending:
        if not _is_notification_chat_allowed(user.id, chat_id):
            await update.message.reply_text("⛔ No tienes acceso a esta acción.")
            return
        result = await asyncio.get_event_loop().run_in_executor(
            None, save_activity_note, pending["activity_id"], user_text
        )
        if result.get("local_saved"):
            await asyncio.get_event_loop().run_in_executor(
                None, db_memory.clear_pending_input, chat_id
            )
            confirmation = "✅ Nota guardada."
            if not result.get("intervals_icu_updated"):
                confirmation += " No se pudo sincronizar con Intervals.icu."
            await update.message.reply_text(confirmation)
        else:
            await update.message.reply_text(
                "⚠️ No se pudo guardar la nota; sigue pendiente y puedes intentarlo de nuevo."
            )
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

    # Python 3.14 no longer creates a default event loop automatically.
    asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("medhist", medhist))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("sync", sync))
    app.add_handler(CommandHandler("injury", injury))
    app.add_handler(CallbackQueryHandler(handle_rpe_callback, pattern=r"^rpe:"))
    app.add_handler(CallbackQueryHandler(handle_note_callback, pattern=r"^note:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def set_commands(_app):
        await _app.bot.set_my_commands([
            ("start", "Inicia el bot y muestra la ayuda"),
            ("medhist", "Gestiona historial médico (set/show/clear)"),
            ("injury", "Evalúa riesgo de lesión (uso: /injury <síntomas>)"),
            ("sync",  "Sincroniza actividades recientes (uso: /sync [días])"),
            ("reset", "Borra el historial de conversación"),
        ])

    app.post_init = set_commands

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
        close_injury_memory()
        db_memory.close()
