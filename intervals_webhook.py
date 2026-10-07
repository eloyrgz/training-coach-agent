import hmac
import html
import logging
import os
import secrets
import threading
import time
from collections.abc import Sequence
from urllib.parse import urlencode

import psycopg2
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

load_dotenv(override=True)

logger = logging.getLogger(__name__)
INTERVALS_WEBHOOK_SECRET = os.getenv("INTERVALS_WEBHOOK_SECRET", "")
_pending_oauth_states: dict[str, float] = {}
_oauth_state_lock = threading.Lock()
_OAUTH_STATE_TTL_SECONDS = 300
_OAUTH_REDIRECT_URI = "https://intervals-webhook.err-hass.duckdns.org/oauth/callback"


class IntervalsActivityEvent(BaseModel):
    athlete_id: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., min_length=1, max_length=64)
    activity: dict[str, object] | None = None


class IntervalsWebhookPayload(BaseModel):
    secret: str = Field(..., min_length=1, max_length=256)
    events: list[IntervalsActivityEvent] = Field(..., max_length=100)


class IntervalsWebhookResponse(BaseModel):
    status: str
    queued: int
    duplicates: int
    ignored: int


def _enqueue_activity_ids(activity_ids: Sequence[str]) -> tuple[int, int]:
    database_uri = os.getenv("SUPABASE_POOLER_DB_URI") or os.getenv("SUPABASE_DB_URI")
    if not database_uri:
        raise RuntimeError("Supabase database URI is not configured")

    connection = psycopg2.connect(database_uri, connect_timeout=10)
    queued = 0
    duplicates = 0
    try:
        with connection.cursor() as cursor:
            for activity_id in activity_ids:
                cursor.execute(
                    """
                    INSERT INTO activity_events (activity_id)
                    VALUES (%s)
                    ON CONFLICT (activity_id) DO NOTHING
                    RETURNING activity_id;
                    """,
                    (activity_id,),
                )
                if cursor.fetchone():
                    queued += 1
                else:
                    duplicates += 1
        connection.commit()
        return queued, duplicates
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


app = FastAPI(
    title="Intervals.icu Webhook Receiver",
    description="Private ingress for Intervals.icu activity webhooks.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health():
    return {"status": "ok"}


def _new_oauth_state() -> str:
    now = time.monotonic()
    with _oauth_state_lock:
        expired = [
            state for state, created_at in _pending_oauth_states.items()
            if now - created_at > _OAUTH_STATE_TTL_SECONDS
        ]
        for state in expired:
            _pending_oauth_states.pop(state, None)
        if len(_pending_oauth_states) >= 10:
            raise HTTPException(status_code=429, detail="Too many pending authorization attempts")
        state = secrets.token_urlsafe(32)
        _pending_oauth_states[state] = now
        return state


def _consume_oauth_state(state: str) -> bool:
    now = time.monotonic()
    with _oauth_state_lock:
        created_at = _pending_oauth_states.pop(state, None)
    return created_at is not None and now - created_at <= _OAUTH_STATE_TTL_SECONDS


def _exchange_oauth_code(code: str) -> dict[str, object]:
    client_id = os.getenv("INTERVALS_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("INTERVALS_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("Intervals OAuth client credentials are not configured")

    response = requests.post(
        "https://intervals.icu/api/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": _OAUTH_REDIRECT_URI,
        },
        timeout=15,
        allow_redirects=False,
    )
    response.raise_for_status()
    token_response = response.json()
    if not isinstance(token_response, dict):
        raise ValueError("Intervals OAuth returned an invalid token response")
    return token_response


def _extract_analyzed_activity_ids(
    events: Sequence[IntervalsActivityEvent],
    expected_athlete_id: str,
) -> tuple[list[str], int]:
    activity_ids = []
    ignored = 0
    for event in events:
        if event.type != "ACTIVITY_ANALYZED" or event.athlete_id != expected_athlete_id:
            ignored += 1
            continue

        activity_id = event.activity.get("id") if event.activity else None
        if not isinstance(activity_id, (str, int)) or isinstance(activity_id, bool):
            ignored += 1
            continue
        activity_id = str(activity_id)
        if not activity_id or len(activity_id) > 128 or activity_id != activity_id.strip():
            ignored += 1
            continue
        activity_ids.append(activity_id)
    return activity_ids, ignored


def _oauth_result_page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Intervals.icu connection</title><body><main><h1>Intervals.icu connection</h1>"
        f"<p>{message}</p></main></body></html>",
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/oauth/start")
def start_oauth_authorization():
    client_id = os.getenv("INTERVALS_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=503, detail="Intervals OAuth client ID is not configured")

    state = _new_oauth_state()
    params = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": "ACTIVITY:READ",
        "state": state,
    })
    return RedirectResponse(f"https://intervals.icu/oauth/authorize?{params}", status_code=302)


@app.get("/oauth/callback", response_class=HTMLResponse)
def finish_oauth_authorization(
    code: str | None = Query(default=None, max_length=512),
    state: str | None = Query(default=None, max_length=128),
    error: str | None = Query(default=None, max_length=128),
):
    if not state or not _consume_oauth_state(state):
        return _oauth_result_page("Authorization state was invalid or expired. Start again.", 400)
    if error or not code:
        return _oauth_result_page("Authorization was not completed. Start again if you still want to connect.", 400)

    try:
        token_response = _exchange_oauth_code(code)
    except RuntimeError:
        return _oauth_result_page("OAuth client credentials are not configured on the service.", 503)
    except (requests.RequestException, ValueError):
        logger.exception("Intervals OAuth token exchange failed")
        return _oauth_result_page("Intervals.icu authorization could not be completed.", 502)

    scope_value = token_response.get("scope")
    granted_scopes = set(scope_value.split(",")) if isinstance(scope_value, str) else set()
    athlete = token_response.get("athlete")
    athlete_id = athlete.get("id") if isinstance(athlete, dict) else None
    if (
        "ACTIVITY:READ" not in granted_scopes
        or not isinstance(athlete_id, (str, int))
        or isinstance(athlete_id, bool)
    ):
        return _oauth_result_page("The app needs ACTIVITY:READ access to receive activity webhooks.", 403)

    safe_athlete_id = html.escape(str(athlete_id))
    return _oauth_result_page(
        "Authorization succeeded. Set <code>INTERVALS_WEBHOOK_ATHLETE_ID="
        f"{safe_athlete_id}</code> in the webhook service environment. "
        "The temporary access token was not stored.",
        200,
    )


@app.post("/webhooks/intervals/activity", response_model=IntervalsWebhookResponse)
def receive_activity_webhook(payload: IntervalsWebhookPayload):
    if not INTERVALS_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Activity webhook is not configured")
    if not hmac.compare_digest(payload.secret, INTERVALS_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    expected_athlete_id = os.getenv("INTERVALS_WEBHOOK_ATHLETE_ID") or os.getenv(
        "INTERVALS_ATHLETE_ID", ""
    )
    if not expected_athlete_id:
        raise HTTPException(status_code=503, detail="Intervals.icu athlete ID is not configured")

    activity_ids, ignored = _extract_analyzed_activity_ids(
        payload.events,
        expected_athlete_id,
    )

    try:
        queued, duplicates = _enqueue_activity_ids(activity_ids) if activity_ids else (0, 0)
    except Exception:
        logger.exception("Could not persist Intervals.icu webhook events")
        raise HTTPException(status_code=503, detail="Could not persist activity events")

    return IntervalsWebhookResponse(
        status="accepted",
        queued=queued,
        duplicates=duplicates,
        ignored=ignored,
    )