import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.tools import tool

from agent_memory import SupabaseAgentMemory
from intervals_icu_client import IntervalsClient

_SUBMODULE_PATH = os.path.join(os.path.dirname(__file__), "plan_generator")
if _SUBMODULE_PATH not in sys.path:
    sys.path.insert(0, _SUBMODULE_PATH)

from plan_generator.config import get_db_uri, get_plan_schema
from plan_generator.db import PlanGeneratorDB

load_dotenv(override=True)

NTC_SOURCE_ZIP = "ntc-catalog"

# NTC workout_type → Intervals.icu activity type
NTC_INTERVALS_TYPE_MAP = {
    "Strength": "WeightTraining",
    "Yoga": "Yoga",
    "Mobility": "Yoga",
    "Cross-Training": "WeightTraining",
    "Endurance": "WeightTraining",
    "Run": "Run",
}

DEFAULT_NTC_DATA_PATH = Path(__file__).parent / "ntc_catalog" / "ntc_output" / "workouts.json"


def _get_db():
    return PlanGeneratorDB(get_db_uri())


def _get_icu_client() -> IntervalsClient | None:
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    if athlete_id and api_key:
        return IntervalsClient(athlete_id=athlete_id, api_key=api_key)
    return None


def _map_ntc_type(workout: dict) -> str:
    """Map NTC metadata fields to a workout_type label."""
    if workout.get("yoga"):
        return "Yoga"
    focus = (workout.get("focus") or "").lower()
    if focus == "strength":
        return "Strength"
    if focus == "mobility":
        return "Mobility"
    if focus == "endurance":
        return "Endurance"
    workout_type = (workout.get("workoutType") or "").upper()
    if workout_type in ("LONG_RUN", "SPEED_RUN", "RECOVERY_RUN"):
        return "Run"
    return "Cross-Training"


def _build_purpose(workout: dict) -> str:
    parts = []
    focus = workout.get("focus") or ""
    if focus:
        parts.append(focus.capitalize())
    mg = workout.get("muscleGroup") or ""
    if mg:
        parts.append(mg)
    intensity = workout.get("intensity") or ""
    if intensity:
        parts.append(intensity)
    return " — ".join(parts) if parts else ""


def import_ntc_catalog(json_path: str | None = None) -> dict:
    """Import NTC workouts from extracted JSON into workout_library.

    Returns dict with counts: total, imported, skipped, errors.
    """
    path = Path(json_path) if json_path else DEFAULT_NTC_DATA_PATH
    with open(path, encoding="utf-8") as f:
        workouts = json.load(f)

    schema = get_plan_schema()
    rows = []
    skipped = 0

    for w in workouts:
        ntc_id = w.get("id", "")
        title = (w.get("title") or "").strip()
        if not title or not ntc_id:
            skipped += 1
            continue

        if w.get("excludeFromLibrary"):
            skipped += 1
            continue

        uid = f"NTC-{ntc_id[:12]}"
        workout_type = _map_ntc_type(w)
        duration_sec = w.get("durationSec") or 0
        duration_minutes = round(duration_sec / 60.0, 2) if duration_sec else None

        metadata = {
            "ntc_type": w.get("type"),
            "guided": w.get("type") == "video",
            "focus": w.get("focus") or "",
            "level": w.get("level") or "",
            "equipment": w.get("equipment") or "",
            "intensity": w.get("intensity") or "",
            "muscleGroup": w.get("muscleGroup") or "",
            "yoga": w.get("yoga", False),
            "workoutType": w.get("workoutType") or "",
            "profiles": w.get("profiles") or [],
            "seoTags": w.get("seoTags") or [],
        }
        if w.get("isPremium"):
            metadata["isPremium"] = True
        if w.get("videoUrl"):
            metadata["videoUrl"] = w["videoUrl"]
        for img_key in ("image_library_url", "image_share_url", "image_postSession_url"):
            if w.get(img_key):
                metadata[img_key] = w[img_key]

        purpose = _build_purpose(w)
        intensity_label = (w.get("intensity") or "").capitalize() or None

        rows.append((
            uid, NTC_SOURCE_ZIP, ntc_id, title, workout_type,
            duration_minutes, None, intensity_label, purpose, None,
            json.dumps(metadata), title,
        ))

    upsert_sql = f"""
    INSERT INTO {schema}.workout_library (
        workout_uid, source_zip, source_file, workout_name, workout_type,
        duration_minutes, tss, intensity, purpose, hr_zones, metadata, raw_text
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
    ON CONFLICT (workout_uid) DO UPDATE SET
        workout_name = EXCLUDED.workout_name,
        workout_type = EXCLUDED.workout_type,
        duration_minutes = EXCLUDED.duration_minutes,
        intensity = EXCLUDED.intensity,
        purpose = EXCLUDED.purpose,
        metadata = EXCLUDED.metadata,
        raw_text = EXCLUDED.raw_text;
    """

    db = _get_db()
    try:
        with db.conn.cursor() as cur:
            cur.executemany(upsert_sql, rows)
        imported = len(rows)
        errors = 0
    except Exception as e:
        imported = 0
        errors = len(rows)
        print(f"Batch import failed: {e}")
    finally:
        db.close()

    return {"total": len(workouts), "imported": imported, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# LLM tools
# ---------------------------------------------------------------------------

@tool
def search_ntc_workouts(
    focus: Optional[str] = None,
    level: Optional[str] = None,
    equipment: Optional[str] = None,
    muscle_group: Optional[str] = None,
    duration_max: Optional[int] = None,
    query: Optional[str] = None,
) -> list:
    """Search the Nike Training Club workout catalog.
    Filters (all optional):
      focus: strength | endurance | mobility
      level: beginner | intermediate | advanced
      equipment: none | basic | full
      muscle_group: glutes | abs | arms | core
      duration_max: max duration in minutes
      query: free-text search on workout name
    Returns up to 10 matching workouts with uid, name, type, duration, and metadata.
    """
    schema = get_plan_schema()
    conditions = [f"source_zip = '{NTC_SOURCE_ZIP}'"]
    params: list = []

    if focus:
        conditions.append("metadata->>'focus' = %s")
        params.append(focus.lower())
    if level:
        conditions.append("metadata->>'level' = %s")
        params.append(level.lower())
    if equipment:
        conditions.append("metadata->>'equipment' = %s")
        params.append(equipment.lower())
    if muscle_group:
        conditions.append("metadata->>'muscleGroup' = %s")
        params.append(muscle_group.lower())
    if duration_max:
        conditions.append("duration_minutes <= %s")
        params.append(duration_max)
    if query:
        conditions.append("workout_name ILIKE %s")
        params.append(f"%{query}%")

    where = " AND ".join(conditions)
    sql = f"""
        SELECT workout_uid, workout_name, workout_type, duration_minutes,
               metadata->>'level' AS level,
               metadata->>'equipment' AS equipment,
               metadata->>'muscleGroup' AS muscle_group,
               intensity
        FROM {schema}.workout_library
        WHERE {where}
        ORDER BY workout_name
        LIMIT 10
    """

    db = _get_db()
    try:
        import psycopg2.extras
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@tool
def schedule_ntc_workout(
    workout_uid: str,
    target_date: str,
    time_of_day: str = "09:00",
) -> dict:
    """Schedule a Nike Training Club workout on a specific date in Intervals.icu.
    Args:
        workout_uid: The NTC workout UID (from search_ntc_workouts results) or the workout name.
        target_date: Date to schedule (YYYY-MM-DD).
        time_of_day: Time of day (HH:MM, default 09:00).
    Returns confirmation with workout name, date, and Intervals.icu activity type.
    """
    schema = get_plan_schema()
    db = _get_db()
    try:
        import psycopg2.extras
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM {schema}.workout_library WHERE workout_uid = %s",
                (workout_uid,),
            )
            row = cur.fetchone()
            if not row:
                # Fallback: match by name (case-insensitive, partial)
                cur.execute(
                    f"SELECT * FROM {schema}.workout_library WHERE source_zip = 'ntc-catalog' AND workout_name ILIKE %s LIMIT 1",
                    (f"%{workout_uid}%",),
                )
                row = cur.fetchone()
    finally:
        db.close()

    if not row:
        return {"error": f"Workout not found: {workout_uid}"}

    client = _get_icu_client()
    if not client:
        return {"error": "Intervals.icu credentials not configured"}

    workout_type = row["workout_type"] or "Cross-Training"
    icu_type = NTC_INTERVALS_TYPE_MAP.get(workout_type, "WeightTraining")
    duration_sec = int((row["duration_minutes"] or 30) * 60)

    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    desc_parts = [row["workout_name"]]
    if row.get("purpose"):
        desc_parts.append(row["purpose"])
    if meta.get("level"):
        desc_parts.append(f"Level: {meta['level']}")
    if meta.get("equipment") and meta["equipment"] != "none":
        desc_parts.append(f"Equipment: {meta['equipment']}")
    desc_parts.append("Nike Training Club")

    event = {
        "start_date_local": f"{target_date}T{time_of_day}:00",
        "category": "WORKOUT",
        "type": icu_type,
        "name": row["workout_name"],
        "description": "\n".join(desc_parts),
        "moving_time": duration_sec,
        "external_id": f"ntc_{workout_uid}_{target_date}",
    }

    client.create_events_bulk([event], upsert=True)

    return {
        "scheduled": True,
        "name": row["workout_name"],
        "date": target_date,
        "time": time_of_day,
        "icu_type": icu_type,
        "duration_min": row["duration_minutes"],
    }


@tool
def suggest_ntc_workout(goal: str) -> list:
    """Suggest Nike Training Club workouts based on a goal and current training load.
    The goal is free-text, e.g. 'recovery day', 'upper body strength', 'yoga for runners',
    'core workout', 'quick 15 min session'.
    Returns up to 3 workout recommendations considering current fatigue (TSB).
    """
    memory = SupabaseAgentMemory()
    metrics = memory.get_latest_metrics()
    tsb = (metrics or {}).get("tsb")

    focus_filter = None
    level_filter = None
    duration_max = None
    query_text = None
    goal_lower = goal.lower()

    if any(kw in goal_lower for kw in ("recovery", "recuperación", "easy", "suave", "relax")):
        focus_filter = "mobility"
        level_filter = "beginner"
    elif any(kw in goal_lower for kw in ("yoga", "stretch", "estirar", "flexibilidad")):
        focus_filter = "mobility"
    elif any(kw in goal_lower for kw in ("strength", "fuerza", "weight", "pesas")):
        focus_filter = "strength"
    elif any(kw in goal_lower for kw in ("cardio", "endurance", "resistencia", "hiit")):
        focus_filter = "endurance"

    for kw in ("core", "abs", "abdominales"):
        if kw in goal_lower:
            query_text = kw
            break
    for kw in ("glutes", "glúteos", "legs", "piernas"):
        if kw in goal_lower:
            query_text = kw if kw in ("glutes", "legs") else None
            break
    for kw in ("arms", "brazos", "upper", "tren superior"):
        if kw in goal_lower:
            query_text = "arms" if "arm" in kw else None
            break

    import re
    dur_match = re.search(r"(\d+)\s*min", goal_lower)
    if dur_match:
        duration_max = int(dur_match.group(1))

    # Adjust based on fatigue
    if tsb is not None and tsb < -10:
        if focus_filter not in ("mobility",):
            focus_filter = focus_filter or "mobility"
        level_filter = level_filter or "beginner"
        if not duration_max or duration_max > 30:
            duration_max = 30

    schema = get_plan_schema()
    conditions = [f"source_zip = '{NTC_SOURCE_ZIP}'"]
    params: list = []

    if focus_filter:
        conditions.append("metadata->>'focus' = %s")
        params.append(focus_filter)
    if level_filter:
        conditions.append("metadata->>'level' = %s")
        params.append(level_filter)
    if duration_max:
        conditions.append("duration_minutes <= %s")
        params.append(duration_max)
    if query_text:
        conditions.append("workout_name ILIKE %s")
        params.append(f"%{query_text}%")

    where = " AND ".join(conditions)
    sql = f"""
        SELECT workout_uid, workout_name, workout_type, duration_minutes,
               metadata->>'level' AS level,
               metadata->>'equipment' AS equipment,
               metadata->>'muscleGroup' AS muscle_group,
               intensity
        FROM {schema}.workout_library
        WHERE {where}
        ORDER BY random()
        LIMIT 3
    """

    db = _get_db()
    try:
        import psycopg2.extras
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        results = [dict(r) for r in rows]
    finally:
        db.close()

    reasoning = ""
    if tsb is not None and tsb < -10:
        reasoning = f"Tu TSB está en {tsb:.0f} (fatiga alta) → priorizo sesiones suaves/cortas."
    elif tsb is not None and tsb > 5:
        reasoning = f"Tu TSB está en {tsb:.0f} (fresco) → puedes con sesiones más intensas."

    return {"reasoning": reasoning, "suggestions": results}


NTC_TOOLS = [search_ntc_workouts, schedule_ntc_workout, suggest_ntc_workout]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    result = import_ntc_catalog(path)
    print(f"Import complete: {result}")
