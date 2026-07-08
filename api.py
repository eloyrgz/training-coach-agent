"""
FastAPI interface for the Training Coach chat agent.

Run:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage

from chat_agent import run_agent
from coach_tools import memory as db_memory
from sync_pipeline import TrainingDataPipeline

load_dotenv(override=True)

# Optional: restrict access via a shared secret in the Authorization header.
API_KEY = os.getenv("COACH_API_KEY", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db_memory.close()


app = FastAPI(
    title="Training Coach API",
    description="Conversational endurance training coach powered by LLM + Supabase.",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Models ---

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="User message to the coach")
    conversation_id: str = Field(default="default", description="Optional session ID to maintain conversation context")


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
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    icu_api_key = os.getenv("INTERVALS_API_KEY")
    supabase_uri = os.getenv("SUPABASE_DB_URI")
    supabase_pooler_uri = os.getenv("SUPABASE_POOLER_DB_URI")

    if not all([athlete_id, icu_api_key, supabase_uri]):
        raise ValueError("Missing configuration in .env. Required: INTERVALS_ATHLETE_ID, INTERVALS_API_KEY, SUPABASE_DB_URI.")

    pipeline = TrainingDataPipeline(
        athlete_id=athlete_id,
        icu_api_key=icu_api_key,
        db_uri=supabase_uri,
        fallback_db_uri=supabase_pooler_uri,
    )
    try:
        pipeline.sync_activities(days_back=days)
    finally:
        pipeline.close()


# --- Endpoints ---

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = None):
    """Send a message to the training coach and get a reply."""
    _check_auth(authorization)

    history = _conversations.get(req.conversation_id, [])
    history.append(HumanMessage(content=req.message))

    try:
        reply = run_agent(history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    history.append(AIMessage(content=reply))
    _conversations[req.conversation_id] = _trim_history(history)

    return ChatResponse(reply=reply, conversation_id=req.conversation_id)


@app.post("/chat/reset")
def reset_conversation(conversation_id: str = "default", authorization: str | None = None):
    """Clear conversation history for a session."""
    _check_auth(authorization)
    _conversations.pop(conversation_id, None)
    return {"status": "cleared", "conversation_id": conversation_id}


@app.post("/sync", response_model=SyncResponse)
def sync(req: SyncRequest, authorization: str | None = None):
    """Sync Intervals.icu activities into Supabase for the requested number of days."""
    _check_auth(authorization)
    try:
        _run_sync(req.days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync error: {e}")
    return SyncResponse(status="ok", days=req.days, message="Sync completed successfully.")
