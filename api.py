"""
FastAPI interface for the Training Coach chat agent.

Run:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import base64
import os
import html
import re
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import logging
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from telegram import Bot
from chat_agent import run_agent, run_weekly_recap_agent
from coach_tools import memory as db_memory
from activity_notifications import ActivityNotificationWorker
from agent_memory import SupabaseAgentMemory
from injury_agent import close_db_memory
from sync_pipeline import build_pipeline_from_env
from weekly_recap_email import INLINE_ICON_CONTENT_IDS, build_weekly_recap_email, summarize_activities

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


class WeeklyRecapRequest(BaseModel):
    week_start: date = Field(..., description="Monday that starts the completed training week")


class WeeklyRecapResponse(BaseModel):
    status: str
    week_start: date
    week_end: date
    provider_message_id: str | None = None


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


def _validate_recap_week(week_start: date, now: datetime | None = None) -> date:
    if week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start must be a Monday")
    madrid = ZoneInfo("Europe/Madrid")
    current_time = (now or datetime.now(madrid)).astimezone(madrid)
    today_madrid = current_time.date()
    current_week_start = today_madrid - timedelta(days=today_madrid.weekday())
    if week_start > current_week_start:
        raise HTTPException(status_code=422, detail="week_start cannot be in the future")
    if week_start == current_week_start and (
        current_time.weekday() != 6
        or current_time.hour < 21
    ):
        raise HTTPException(
            status_code=422,
            detail="the current week can only be recapped on Sunday from 21:00 Europe/Madrid",
        )
    return week_start + timedelta(days=6)


def _send_weekly_recap_email(
    week_start: date,
    week_end: date,
    text_content: str,
    html_content: str,
) -> str:
    api_key = os.getenv("RESEND_API_KEY", "")
    sender = os.getenv("WEEKLY_RECAP_EMAIL_FROM", "")
    recipient = os.getenv("WEEKLY_RECAP_EMAIL_TO", "")
    if not api_key or not sender or not recipient:
        raise RuntimeError(
            "RESEND_API_KEY, WEEKLY_RECAP_EMAIL_FROM, and WEEKLY_RECAP_EMAIL_TO are required"
        )

    subject = f"Resumen de entrenamiento: Semana {week_start.isocalendar().week}"
    icon_dir = Path(__file__).resolve().parent / "assets"
    icon_attachments = [
        {
            "filename": f"{content_id}.png",
            "content": base64.b64encode((icon_dir / f"{content_id}.png").read_bytes()).decode("ascii"),
            "content_id": content_id,
        }
        for content_id in INLINE_ICON_CONTENT_IDS
    ]
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": f"weekly-recap-{week_start.isoformat()}",
        },
        json={
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "text": text_content,
            "html": html_content,
            "attachments": icon_attachments,
        },
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError(f"Resend returned HTTP {response.status_code}")
    message_id = response.json().get("id")
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("Resend response did not contain an email ID")
    return message_id


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


@app.post("/weekly-recap", response_model=WeeklyRecapResponse)
def weekly_recap(
    req: WeeklyRecapRequest,
    authorization: str | None = Header(default=None),
):
    """Generate and email the recap for one completed Monday-Sunday week."""
    _check_auth(authorization)
    week_end = _validate_recap_week(req.week_start)

    try:
        claimed = db_memory.claim_weekly_recap(req.week_start)
    except Exception as error:
        logger.exception("Could not claim weekly recap %s", req.week_start)
        raise HTTPException(status_code=503, detail="Could not access weekly recap ledger") from error
    if not claimed:
        return WeeklyRecapResponse(
            status="already_sent_or_processing",
            week_start=req.week_start,
            week_end=week_end,
        )

    try:
        activities = db_memory.get_activities_in_range(
            req.week_start.isoformat(), week_end.isoformat(), limit=100, sort_by="date"
        )
        activity_summary = summarize_activities(activities)
        weekly_trend = db_memory.get_weekly_load_trend(req.week_start, week_end)
        daily_wellness = db_memory.get_daily_wellness(req.week_start, week_end)
        narrative = run_weekly_recap_agent(
            req.week_start.isoformat(),
            week_end.isoformat(),
            {
                "this_week": activity_summary,
                "recent_weekly_trends": weekly_trend,
                "daily_health_metrics": daily_wellness,
            },
        )
        html_content, text_content = build_weekly_recap_email(
            req.week_start, week_end, activities, activity_summary, narrative,
            daily_wellness, weekly_trend,
        )
        db_memory.set_weekly_recap_content(req.week_start, text_content)
        provider_message_id = _send_weekly_recap_email(
            req.week_start, week_end, text_content, html_content
        )
        if not db_memory.complete_weekly_recap(req.week_start, provider_message_id):
            raise RuntimeError("Email was accepted but recap delivery state was not persisted")
    except Exception as error:
        try:
            db_memory.fail_weekly_recap(req.week_start, str(error))
        except Exception:
            logger.exception("Could not record weekly recap failure for %s", req.week_start)
        logger.exception("Could not deliver weekly recap for %s", req.week_start)
        raise HTTPException(status_code=502, detail="Weekly recap generation or delivery failed") from error

    return WeeklyRecapResponse(
        status="sent",
        week_start=req.week_start,
        week_end=week_end,
        provider_message_id=provider_message_id,
    )


