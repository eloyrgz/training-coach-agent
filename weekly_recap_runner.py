"""Call the coach API for the previous completed training week."""

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv


API_URL = os.getenv("COACH_API_URL", "http://127.0.0.1:8000")


def previous_week_start(today: date | None = None) -> date:
    local_today = today or datetime.now(ZoneInfo("Europe/Madrid")).date()
    current_monday = local_today - timedelta(days=local_today.weekday())
    return current_monday - timedelta(days=7)


def main() -> None:
    load_dotenv(override=True)
    api_key = os.getenv("COACH_API_KEY", "")
    if not api_key:
        raise SystemExit("COACH_API_KEY is required")

    week_start = previous_week_start()
    response = httpx.post(
        f"{API_URL.rstrip('/')}/weekly-recap",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"week_start": week_start.isoformat()},
        timeout=900,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("status") not in ("sent", "already_sent_or_processing"):
        raise SystemExit(f"Unexpected weekly recap response: {result.get('status')!r}")
    print(f"Weekly recap {result['status']} for {week_start.isoformat()}")


if __name__ == "__main__":
    main()