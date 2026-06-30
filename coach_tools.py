import os
import requests
from datetime import date, timedelta
from typing import Optional
from langchain_core.tools import tool
from dotenv import load_dotenv

from agent_memory import SupabaseAgentMemory

load_dotenv(override=True)

memory = SupabaseAgentMemory()


@tool
def get_latest_metrics() -> dict:
    """Get the most recent activity with current CTL/ATL/TSB fitness metrics."""
    result = memory.get_latest_metrics()
    return result or {"error": "No metrics found."}


@tool
def query_activities(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    activity_type: Optional[str] = None,
    sort_by: str = "date",
    limit: int = 10,
) -> list:
    """
    Query a list of activities. Params: start_date/end_date (YYYY-MM-DD),
    activity_type: Run | TrailRun | Ride | MountainBikeRide | Swim | Walk | Hike | WeightTraining | Yoga | StandUpPaddling | RockClimbing,
    sort_by (date/distance/duration/elevation/load), limit (default 10).
    USE THIS for 'top N' questions: e.g. '4 longest runs' → sort_by='distance', activity_type='Run', limit=4.
    IMPORTANT: always pass activity_type when the question is sport-specific.
    WeightTraining, Yoga and similar have null distance — always filter by activity_type to avoid mixing them in distance/duration queries.
    Returns full activity details including date, name, distance, duration, CTL/ATL/TSB.
    """
    if start_date or end_date:
        start = start_date or "2000-01-01"
        end = end_date or "2099-12-31"
        return memory.get_activities_in_range(start, end, activity_type, min(limit, 100), sort_by)
    return memory.get_recent_activities(min(limit, 100), sort_by)


@tool
def search_training_logs(query: str) -> list:
    """Semantic search over workout notes and injury logs. Use for subjective info: pain, feelings, injuries."""
    return memory.get_injury_context(query, threshold=0.2, limit=5)


@tool
def get_personal_bests(activity_type: Optional[str] = None) -> dict:
    """Lifetime SINGLE best values: max distance ever, longest duration ever, highest elevation ever, peak CTL ever.
    Returns ONE aggregate row — NOT a list of activities.
    Do NOT use this for 'top N' questions. Use query_activities with sort_by for those.
    """
    return memory.get_personal_bests(activity_type)


@tool
def get_volume_summary(group_by: str, start_date: str, end_date: str, activity_type: Optional[str] = None) -> list:
    """Aggregated volume by 'week' or 'month': total km, hours, elevation, avg CTL/ATL/TSB."""
    return memory.get_volume_summary(group_by, start_date, end_date, activity_type)


@tool
def get_ctl_trend(start_date: str, end_date: str) -> list:
    """Weekly CTL/ATL/TSB evolution over a date range. Use for fitness progression questions."""
    return memory.get_ctl_trend(start_date, end_date)


@tool
def get_sport_breakdown(start_date: str, end_date: str) -> list:
    """Total km, hours and activity count per sport type in a date range."""
    return memory.get_sport_breakdown(start_date, end_date)


@tool
def get_rest_days(start_date: str, end_date: str) -> dict:
    """Active vs rest days in a date range with activity percentage."""
    return memory.get_rest_days(start_date, end_date)


@tool
def compare_periods(start_a: str, end_a: str, start_b: str, end_b: str, activity_type: Optional[str] = None) -> dict:
    """Compare two training periods side by side: volume, CTL/ATL/TSB, RPE."""
    return memory.compare_periods(start_a, end_a, start_b, end_b, activity_type)


@tool
def get_streak() -> dict:
    """Current training streak and longest historical streak in consecutive days."""
    return memory.get_streak()


@tool
def add_training_note(note: str, note_date: Optional[str] = None) -> dict:
    """
    Save a subjective training note (pain, feelings, sensations) for a workout.
    Saves to local DB with semantic search AND posts as a comment on the Intervals.icu activity.
    Use for: 'sentí dolor en la rodilla', 'me sentí muy bien', 'tuve molestias', 'añade una nota'.
    note_date: YYYY-MM-DD (defaults to today).
    """
    target_date = note_date or date.today().isoformat()

    # 1. Save locally with embedding for semantic search
    local_result = memory.add_training_note(note, target_date)

    # 2. Also post as a comment on the Intervals.icu activity (if one exists that day)
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    icu_result = {"intervals_icu_comment": False}

    if athlete_id and api_key:
        activities = memory.get_activity_ids_for_date(target_date)
        if activities:
            act_id = activities[0]["activity_id"]
            try:
                r = requests.post(
                    f"https://intervals.icu/api/v1/activity/{act_id}/messages",
                    auth=("API_KEY", api_key),
                    json={"message": note},
                    timeout=10,
                )
                r.raise_for_status()
                icu_result = {"intervals_icu_comment": True, "activity": activities[0]["activity_name"]}
            except requests.RequestException as e:
                icu_result = {"intervals_icu_comment": False, "intervals_icu_error": str(e)}

    return {**local_result, **icu_result}


@tool
def get_scheduled_workouts(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """
    Fetch FUTURE or PLANNED workouts from Intervals.icu schedule.
    Use for: 'workout del día', 'qué tengo programado', 'plan de esta semana', 'próximos entrenamientos'.
    start_date/end_date: YYYY-MM-DD. Defaults to today when not provided.
    Returns planned workout name, type, date, planned distance, duration, and description.
    """
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    if not athlete_id or not api_key:
        return [{"error": "Missing INTERVALS_ATHLETE_ID or INTERVALS_API_KEY in environment."}]

    today = date.today().isoformat()
    oldest = start_date or today
    newest = end_date or today

    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/events"
    params = {"oldest": oldest, "newest": newest}

    try:
        response = requests.get(url, auth=("API_KEY", api_key), params=params, timeout=10)
        response.raise_for_status()
        events = response.json()
    except requests.RequestException as e:
        return [{"error": f"Failed to fetch scheduled workouts: {e}"}]

    results = []
    for ev in events:
        category = ev.get("category", "")
        # Skip non-workout entries (notes, races without plan, etc.) but include all scheduled events
        results.append({
            "date": (ev.get("start_date_local") or "")[:10],
            "name": ev.get("name"),
            "type": ev.get("type"),
            "category": category,
            "planned_distance_m": ev.get("distance"),
            "planned_duration_s": ev.get("moving_time"),
            "description": ev.get("description") or ev.get("athlete_comment"),
        })

    return results if results else [{"message": f"No workouts scheduled between {oldest} and {newest}."}]


def _fetch_planned(target_date: str) -> list:
    """Internal helper: fetch Intervals.icu planned events for a single date."""
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    if not athlete_id or not api_key:
        return []
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/events"
    try:
        r = requests.get(url, auth=("API_KEY", api_key),
                         params={"oldest": target_date, "newest": target_date}, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return []


@tool
def log_rpe(rpe: int, target_date: Optional[str] = None, activity_name: Optional[str] = None) -> dict:
    """
    Save RPE (Rate of Perceived Exertion, 1-10) for a workout directly to Intervals.icu AND local DB.
    Use for: 'el entrenamiento fue un 7 de esfuerzo', 'registra mi RPE', 'fue muy duro/fácil'.
    rpe: integer 1-10. target_date: YYYY-MM-DD (defaults to today).
    activity_name: optional hint to pick the right activity if multiple exist that day.
    Returns confirmation with activity name and updated RPE.
    """
    if not 1 <= rpe <= 10:
        return {"error": "RPE must be between 1 and 10."}

    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    if not athlete_id or not api_key:
        return {"error": "Missing INTERVALS_ATHLETE_ID or INTERVALS_API_KEY."}

    target = target_date or date.today().isoformat()

    activities = memory.get_activity_ids_for_date(target)
    if not activities:
        return {"error": f"No activities found in local DB for {target}. Run sync first."}

    # Pick the best match if activity_name hint is provided
    act = activities[0]
    if activity_name and len(activities) > 1:
        hint = activity_name.lower()
        for a in activities:
            if hint in (a.get("activity_name") or "").lower():
                act = a
                break

    activity_id = act["activity_id"]
    name = act["activity_name"]

    # --- Update Intervals.icu ---
    url = f"https://intervals.icu/api/v1/activity/{activity_id}"
    try:
        r = requests.put(
            url,
            auth=("API_KEY", api_key),
            json={"icu_rpe": rpe},
            timeout=10,
        )
        r.raise_for_status()
        icu_ok = True
    except requests.RequestException as e:
        icu_ok = False
        icu_error = str(e)

    # --- Update local DB ---
    local_ok = memory.update_rpe_local(activity_id, rpe)

    result = {
        "date": target,
        "activity": name,
        "activity_id": activity_id,
        "rpe_saved": rpe,
        "intervals_icu_updated": icu_ok,
        "local_db_updated": local_ok,
    }
    if not icu_ok:
        result["intervals_icu_error"] = icu_error
    return result


@tool
def compare_planned_vs_actual(target_date: Optional[str] = None) -> dict:
    """
    Compare the PLANNED workout against the ACTUAL activity logged for a given date.
    Use for: 'cómo fue vs el plan', 'cumplí el entrenamiento', 'planned vs actual', 'hice lo que tocaba'.
    target_date: YYYY-MM-DD (defaults to today).
    Returns side-by-side planned and actual metrics: distance, duration, load, RPE, and compliance summary.
    """
    target = target_date or date.today().isoformat()

    # --- Planned ---
    events = _fetch_planned(target)
    planned_list = [
        {
            "name": ev.get("name"),
            "type": ev.get("type"),
            "planned_distance_m": ev.get("distance"),
            "planned_duration_s": ev.get("moving_time"),
            "description": ev.get("description") or ev.get("athlete_comment"),
        }
        for ev in events if ev.get("category") == "WORKOUT" or ev.get("type")
    ]

    # --- Actual ---
    actual_list = memory.get_activities_in_range(target, target, None, 10, "date")
    actual_summary = [
        {
            "name": a.get("activity_name"),
            "type": a.get("activity_type"),
            "actual_distance_m": a.get("distance_meters"),
            "actual_duration_s": a.get("moving_time_seconds"),
            "elevation_m": a.get("elevation_gain_meters"),
            "rpe": a.get("rpe"),
            "load": a.get("icu_load"),
        }
        for a in (actual_list or [])
    ]

    if not planned_list and not actual_summary:
        return {"message": f"No planned or actual workouts found for {target}."}

    # --- Compliance deltas (first planned vs first matching actual) ---
    comparison = {"date": target, "planned": planned_list, "actual": actual_summary}
    if planned_list and actual_summary:
        p = planned_list[0]
        a = actual_summary[0]
        pd_ = p.get("planned_distance_m")
        ad_ = a.get("actual_distance_m")
        pt_ = p.get("planned_duration_s")
        at_ = a.get("actual_duration_s")
        comparison["delta"] = {
            "distance_m": round(ad_ - pd_, 1) if pd_ and ad_ else None,
            "distance_pct": round((ad_ / pd_ - 1) * 100, 1) if pd_ and ad_ else None,
            "duration_s": round(at_ - pt_, 0) if pt_ and at_ else None,
            "duration_pct": round((at_ / pt_ - 1) * 100, 1) if pt_ and at_ else None,
        }

    return comparison


@tool
def get_medical_background() -> list:
    """Get the athlete's stored medical background (prior injuries, surgeries, chronic conditions).
    Use when the user asks about their injury history, medical conditions, or when providing injury advice."""
    entries = memory.get_medical_background()
    if not entries:
        return [{"message": "No medical background stored."}]
    return entries


ALL_TOOLS = [
    get_latest_metrics,
    query_activities,
    search_training_logs,
    get_personal_bests,
    get_volume_summary,
    get_ctl_trend,
    get_sport_breakdown,
    get_rest_days,
    compare_periods,
    get_streak,
    add_training_note,
    get_scheduled_workouts,
    compare_planned_vs_actual,
    log_rpe,
    get_medical_background,
]
