"""
FastAPI interface for the Training Coach chat agent.

Run:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import html
import re
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import logging
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from telegram import Bot
from chat_agent import run_agent
from coach_tools import memory as db_memory
from activity_notifications import ActivityNotificationWorker
from agent_memory import SupabaseAgentMemory
from injury_agent import close_db_memory
from sync_pipeline import build_pipeline_from_env

load_dotenv(override=True)
logger = logging.getLogger(__name__)

# Optional: restrict access via a shared secret in the Authorization header.
API_KEY = os.getenv("COACH_API_KEY", "")


def _notification_chat_id() -> int | None:
    raw_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    try:
        chat_ids = {int(value.strip()) for value in raw_ids.split(",") if value.strip()}
    except ValueError:
        logger.warning("Invalid TELEGRAM_ALLOWED_USER_IDS; activity worker disabled")
        return None
    if len(chat_ids) != 1:
        return None
    chat_id = next(iter(chat_ids))
    return chat_id if chat_id > 0 else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    notification_task = None
    notification_bot = None
    notification_memory = None
    chat_id = _notification_chat_id()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    if bot_token and chat_id:
        try:
            notification_memory = SupabaseAgentMemory()
            notification_bot = Bot(bot_token)
            await notification_bot.initialize()
            worker = ActivityNotificationWorker(
                notification_memory,
                notification_bot,
                chat_id,
                sync_missing_activity=lambda: _run_sync(2),
            )
            notification_task = asyncio.create_task(worker.run(stop_event))
        except Exception:
            logger.exception("Could not start activity notification worker")
            if notification_bot:
                await notification_bot.shutdown()
            if notification_memory:
                notification_memory.close()
            notification_bot = None
            notification_memory = None

    try:
        yield
    finally:
        if notification_task:
            stop_event.set()
            await notification_task
        if notification_bot:
            await notification_bot.shutdown()
        if notification_memory:
            notification_memory.close()
        try:
            db_memory.close()
        finally:
            close_db_memory()


app = FastAPI(
    title="Training Coach API",
    description="Conversational endurance training coach powered by LLM + Supabase.",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Models ---

class HistoryTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="User message to the coach")
    conversation_id: str = Field(default="default", description="Optional session ID to maintain conversation context")
    history: list[HistoryTurn] | None = Field(
        default=None,
        description="Prior turns supplied by the caller; overrides the in-memory store when present",
    )
    context: str | None = Field(default=None, max_length=8000, description="Extra memory context (e.g. user facts)")


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str


class SyncRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=3650, description="Number of days to sync from Intervals.icu")


class SyncResponse(BaseModel):
    status: str
    days: int
    message: str


# --- In-memory conversation store (keyed by conversation_id) ---

_conversations: dict[str, list] = {}
MAX_HISTORY_TURNS = 10


def _trim_history(history: list) -> list:
    pairs = []
    for msg in history:
        if isinstance(msg, (HumanMessage, AIMessage)):
            pairs.append(msg)
    return pairs[-(MAX_HISTORY_TURNS * 2):]


# --- Auth helper ---

def _check_auth(authorization: str | None):
    if API_KEY and (not authorization or authorization != f"Bearer {API_KEY}"):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _run_sync(days: int):
    pipeline = build_pipeline_from_env()
    try:
        pipeline.sync_activities(days_back=days)
    finally:
        pipeline.close()


# --- Endpoints ---

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ntc/workout/{workout_id}", response_class=HTMLResponse)
def ntc_workout_link_page(workout_id: str):
    """Provide a tap-friendly HTTPS handoff to an NTC workout on Android."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", workout_id):
        raise HTTPException(status_code=404, detail="Workout not found")

    safe_id = html.escape(workout_id, quote=True)
    native_url = f"niketrainingclub://x-callback-url/workout?id={safe_id}"
    intent_url = (
        f"intent://x-callback-url/workout?id={safe_id}"
        "#Intent;scheme=niketrainingclub;package=com.nike.ntc;end"
    )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open workout in Nike Training Club</title>
<style>
body {{ font: 16px system-ui, sans-serif; max-width: 36rem; margin: 12vh auto; padding: 0 1rem; color: #171717; }}
a {{ display: block; margin: 1rem 0; padding: 1rem; border-radius: .6rem; background: #111; color: white; text-align: center; text-decoration: none; font-weight: 700; }}
</style></head>
<body><h1>Nike Training Club workout</h1>
<p>Workout ID: {safe_id}</p>
<a href="{intent_url}">Open in NTC</a>
<p>If the button does not open the app, <a href="{native_url}">try the Nike app link</a>.</p>
</body></html>"""


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    """Send a message to the training coach and get a reply."""
    _check_auth(authorization)

    if req.history is not None:
        history = [
            HumanMessage(content=turn.content) if turn.role == "user" else AIMessage(content=turn.content)
            for turn in req.history
            if turn.role in ("user", "assistant")
        ]
    else:
        history = _conversations.get(req.conversation_id, [])
    history.append(HumanMessage(content=req.message))

    agent_messages = ([SystemMessage(content=req.context)] if req.context else []) + history

    try:
        reply = run_agent(agent_messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    history.append(AIMessage(content=reply))
    _conversations[req.conversation_id] = _trim_history(history)

    return ChatResponse(reply=reply, conversation_id=req.conversation_id)


@app.post("/chat/reset")
def reset_conversation(
    conversation_id: str = "default",
    authorization: str | None = Header(default=None),
):
    """Clear conversation history for a session."""
    _check_auth(authorization)
    _conversations.pop(conversation_id, None)
    return {"status": "cleared", "conversation_id": conversation_id}


@app.post("/sync", response_model=SyncResponse)
def sync(req: SyncRequest, authorization: str | None = Header(default=None)):
    """Sync Intervals.icu activities into Supabase for the requested number of days."""
    _check_auth(authorization)
    try:
        _run_sync(req.days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync error: {e}")
    return SyncResponse(status="ok", days=req.days, message="Sync completed successfully.")


