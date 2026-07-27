"""Plan generation tools exposed to the LLM agent.

Wraps plan_generator submodule functionality as LangChain @tool functions
so the chat agent can generate, list, and manage training plans conversationally.
"""
from __future__ import annotations

import sys
import os
from typing import Optional
from langchain_core.tools import tool
from dotenv import load_dotenv

from intervals_icu_client import IntervalsClient, IntervalsAPIError

# Add submodule to sys.path so plan_generator package is importable
_SUBMODULE_PATH = os.path.join(os.path.dirname(__file__), "plan_generator")
if _SUBMODULE_PATH not in sys.path:
    sys.path.insert(0, _SUBMODULE_PATH)

from plan_generator.db import PlanGeneratorDB
from plan_generator.config import get_db_uri, get_plan_schema
from plan_generator.plan_engine import generate_plan as _engine_generate_plan

load_dotenv(override=True)


def _get_db() -> PlanGeneratorDB:
    return PlanGeneratorDB(get_db_uri())


@tool
def list_blueprints() -> list:
    """List available training plan blueprints (source templates like Level 1, Level 2).
    Returns blueprint id, name, race distance, and number of weeks."""
    schema = get_plan_schema()
    db = _get_db()
    try:
        rows = db.fetchall(
            f"""
            SELECT id, blueprint_name, race_distance_km, total_weeks
            FROM {schema}.plan_blueprints
            ORDER BY id
            """
        )
        return [
            {"id": r[0], "name": r[1], "race_distance_km": r[2], "total_weeks": r[3]}
            for r in rows
        ]
    finally:
        db.close()


@tool
def generate_training_plan(
    blueprint_id: int,
    label: str,
    target_weekly_hours: float,
    available_weeks: int = 18,
    current_ctl: float = 0.0,
    max_long_run_hours: float = 4.0,
    preferred_long_run_day: str = "Sun",
    max_sessions: Optional[int] = None,
    unavailable_days: Optional[list[str]] = None,
    race_distance_km: float = 50.0,
) -> dict:
    """Generate a personalized training plan based on a blueprint template.

    Args:
        blueprint_id: Which source blueprint to use (get from list_blueprints).
        label: Free-text label for this plan, e.g. "Eloy 2027 Ultra 50K".
        target_weekly_hours: Average training hours per week (3.5–12.0).
        available_weeks: Plan length in weeks (10–22, default 18).
        current_ctl: Athlete's current CTL/fitness; high values skip early base weeks.
        max_long_run_hours: Cap on longest single run in hours (default 4.0).
        preferred_long_run_day: Day for the long run (Mon/Tue/Wed/Thu/Fri/Sat/Sun).
        max_sessions: Max sessions per week (drops lowest-priority slots if set).
        unavailable_days: Days to skip entirely, e.g. ["Wed", "Fri"].
        race_distance_km: Target race distance (default 50).

    Returns plan instance id, week summaries, and validation results.
    """
    schema = get_plan_schema()
    db = _get_db()
    try:
        result = _engine_generate_plan(
            db=db,
            schema=schema,
            blueprint_id=blueprint_id,
            athlete_label=label,
            available_weeks=available_weeks,
            target_hours=target_weekly_hours,
            current_ctl=current_ctl,
            max_long_run_h=max_long_run_hours,
            preferred_long_run_day=preferred_long_run_day,
            max_sessions=max_sessions,
            unavailable_days=unavailable_days,
            race_distance_km=race_distance_km,
        )
        return result
    finally:
        db.close()


@tool
def list_plans(limit: int = 5) -> list:
    """List existing generated training plan instances (most recent first).
    Returns plan id, label, race distance, weeks, target hours, and creation date."""
    schema = get_plan_schema()
    db = _get_db()
    try:
        rows = db.fetchall(
            f"""
            SELECT id, athlete_label, race_distance_km,
                   available_weeks, target_weekly_hours, created_at
            FROM {schema}.plan_instances
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [
            {
                "id": r[0],
                "label": r[1],
                "race_distance_km": r[2],
                "available_weeks": r[3],
                "target_weekly_hours": float(r[4]) if r[4] else None,
                "created_at": str(r[5]),
            }
            for r in rows
        ]
    finally:
        db.close()


@tool
def get_plan_summary(instance_id: Optional[int] = None) -> dict:
    """Get a week-by-week summary of a generated plan.
    If instance_id is omitted, returns the most recent plan.
    Shows each week's phase, workouts, types, and durations."""
    schema = get_plan_schema()
    db = _get_db()
    try:
        # Get instance info
        if instance_id is None:
            inst_rows = db.fetchall(
                f"""
                SELECT id, athlete_label, race_distance_km,
                       available_weeks, target_weekly_hours, created_at
                FROM {schema}.plan_instances
                ORDER BY id DESC LIMIT 1
                """
            )
        else:
            inst_rows = db.fetchall(
                f"""
                SELECT id, athlete_label, race_distance_km,
                       available_weeks, target_weekly_hours, created_at
                FROM {schema}.plan_instances
                WHERE id = %s LIMIT 1
                """,
                (instance_id,),
            )

        if not inst_rows:
            return {"error": f"No plan instance found{f' with id={instance_id}' if instance_id else ''}"}

        inst = inst_rows[0]
        plan_id = inst[0]

        # Get week metadata
        weeks = db.fetchall(
            f"""
            SELECT week_number, phase_name, target_hours, is_recovery
            FROM {schema}.plan_instance_weeks
            WHERE plan_instance_id = %s
            ORDER BY week_number
            """,
            (plan_id,),
        )

        # Get workouts
        workouts = db.fetchall(
            f"""
            SELECT w.week_number, w.day_name, w.workout_uid, w.notes,
                   l.workout_name, l.workout_type, l.duration_minutes, l.intensity
            FROM {schema}.plan_instance_workouts w
            LEFT JOIN {schema}.workout_library l ON l.workout_uid = w.workout_uid
            WHERE w.plan_instance_id = %s
            ORDER BY w.week_number, w.day_name
            """,
            (plan_id,),
        )

        # Group workouts by week
        weeks_data = {}
        for wk in weeks:
            weeks_data[wk[0]] = {
                "week": wk[0],
                "phase": wk[1],
                "target_hours": float(wk[2]) if wk[2] else None,
                "is_recovery": wk[3],
                "workouts": [],
            }

        for wo in workouts:
            wk_num = wo[0]
            if wk_num not in weeks_data:
                weeks_data[wk_num] = {"week": wk_num, "workouts": []}
            weeks_data[wk_num]["workouts"].append({
                "day": wo[1],
                "name": wo[4],
                "type": wo[5],
                "duration_min": wo[6],
                "intensity": wo[7],
            })

        return {
            "plan_id": plan_id,
            "label": inst[1],
            "race_distance_km": inst[2],
            "available_weeks": inst[3],
            "target_weekly_hours": float(inst[4]) if inst[4] else None,
            "created_at": str(inst[5]),
            "weeks": list(weeks_data.values()),
        }
    finally:
        db.close()


@tool
def push_plan_to_intervals(
    instance_id: Optional[int] = None,
    start_date: Optional[str] = None,
) -> dict:
    """Push a generated plan to the Intervals.icu calendar as scheduled events.
    If instance_id is omitted, pushes the most recent plan.
    start_date (YYYY-MM-DD) sets when the plan begins; defaults to next Monday.

    Requires INTERVALS_API_KEY and INTERVALS_ATHLETE_ID in environment.
    """
    from datetime import date, timedelta

    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")
    if not api_key or not athlete_id:
        return {"error": "INTERVALS_API_KEY and INTERVALS_ATHLETE_ID must be set in .env"}

    client = IntervalsClient(athlete_id=athlete_id, api_key=api_key)

    schema = get_plan_schema()
    db = _get_db()
    try:
        # Resolve instance
        if instance_id is None:
            rows = db.fetchall(
                f"SELECT id FROM {schema}.plan_instances ORDER BY id DESC LIMIT 1"
            )
            if not rows:
                return {"error": "No plan instances found"}
            instance_id = rows[0][0]

        # Get workouts for this plan
        workouts = db.fetchall(
            f"""
            SELECT w.week_number, w.day_name, w.workout_uid, w.notes,
                   l.workout_name, l.workout_type, l.duration_minutes
            FROM {schema}.plan_instance_workouts w
            LEFT JOIN {schema}.workout_library l ON l.workout_uid = w.workout_uid
            WHERE w.plan_instance_id = %s
            ORDER BY w.week_number, w.day_name
            """,
            (instance_id,),
        )

        if not workouts:
            return {"error": f"No workouts found for plan instance {instance_id}"}

        # Compute start date (default: next Monday)
        if start_date:
            plan_start = date.fromisoformat(start_date)
        else:
            today = date.today()
            days_until_monday = (7 - today.weekday()) % 7 or 7
            plan_start = today + timedelta(days=days_until_monday)

        # Map day names to offsets (Mon=0, Sun=6)
        day_offsets = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

        # Build events
        events = []
        for wo in workouts:
            week_num, day_name, workout_uid, notes, name, wtype, duration = wo
            day_offset = day_offsets.get(day_name, 0)
            event_date = plan_start + timedelta(weeks=week_num - 1, days=day_offset)

            event = {
                "start_date_local": event_date.isoformat(),
                "category": "WORKOUT",
                "name": name or f"{wtype} workout",
                "description": notes or "",
                "moving_time": int((duration or 60) * 60),
                "external_id": f"plan_{instance_id}_w{week_num}_{day_name}_{workout_uid}",
            }
            events.append(event)

        # Push in batches of 50
        created = []
        batch_size = 50
        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            result = client.create_events_bulk(batch)
            created.extend(result if isinstance(result, list) else [result])

        return {
            "status": "ok",
            "plan_instance_id": instance_id,
            "start_date": plan_start.isoformat(),
            "events_created": len(created),
            "weeks_covered": workouts[-1][0] if workouts else 0,
        }
    finally:
        db.close()


@tool
def delete_plan(instance_id: Optional[int] = None) -> dict:
    """Delete a generated training plan and all its associated data.
    If instance_id is omitted, deletes the most recent plan.
    Also removes the corresponding events from Intervals.icu if they were pushed.

    Returns the deleted plan's id and label.
    """
    schema = get_plan_schema()
    db = _get_db()
    try:
        # Resolve instance
        if instance_id is None:
            rows = db.fetchall(
                f"SELECT id, athlete_label FROM {schema}.plan_instances ORDER BY id DESC LIMIT 1"
            )
        else:
            rows = db.fetchall(
                f"SELECT id, athlete_label FROM {schema}.plan_instances WHERE id = %s",
                (instance_id,),
            )

        if not rows:
            return {"error": f"No plan instance found{f' with id={instance_id}' if instance_id else ''}"}

        plan_id, label = rows[0]

        # Collect external_ids for Intervals.icu cleanup
        workout_rows = db.fetchall(
            f"""
            SELECT week_number, day_name, workout_uid
            FROM {schema}.plan_instance_workouts
            WHERE plan_instance_id = %s
            """,
            (plan_id,),
        )
        external_ids = [
            f"plan_{plan_id}_w{r[0]}_{r[1]}_{r[2]}" for r in workout_rows
        ]

        # Delete from DB (cascade: workouts and weeks)
        db.execute(
            f"DELETE FROM {schema}.plan_instance_workouts WHERE plan_instance_id = %s",
            (plan_id,),
        )
        db.execute(
            f"DELETE FROM {schema}.plan_instance_weeks WHERE plan_instance_id = %s",
            (plan_id,),
        )
        db.execute(
            f"DELETE FROM {schema}.plan_instances WHERE id = %s",
            (plan_id,),
        )

        # Try to remove from Intervals.icu calendar
        icu_deleted = 0
        athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
        api_key = os.getenv("INTERVALS_API_KEY")
        if athlete_id and api_key and external_ids:
            try:
                client = IntervalsClient(athlete_id=athlete_id, api_key=api_key)
                client.delete_events_bulk(external_ids)
                icu_deleted = len(external_ids)
            except IntervalsAPIError:
                pass  # Best-effort cleanup

        return {
            "status": "ok",
            "deleted_plan_id": plan_id,
            "label": label,
            "workouts_removed": len(workout_rows),
            "intervals_events_removed": icu_deleted,
        }
    finally:
        db.close()


PLAN_TOOLS = [
    list_blueprints,
    generate_training_plan,
    list_plans,
    get_plan_summary,
    push_plan_to_intervals,
    delete_plan,
]
