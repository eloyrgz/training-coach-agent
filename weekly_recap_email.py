"""Render a structured weekly training recap for email clients."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
import re


_WEEKDAYS = (
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
)

_SUMMARY_HEADING_RE = re.compile(
    r"^\s*(?:resumen|recapitulación)\s+(?:de\s+)?la\s+semana"
    r"(?:\s*\([^)]*\))?\s*[:：.\-–—]?\s*",
    re.IGNORECASE,
)

INLINE_ICON_CONTENT_IDS = (
    "mdi-activity-55d9ad",
    "mdi-bike-f2c45d",
    "mdi-chart-f4c65e",
    "mdi-clock-55c9f0",
    "mdi-dumbbell-f2c45d",
    "mdi-heart-55d9ad",
    "mdi-lotus-c49bff",
    "mdi-mountain-c49bff",
    "mdi-moon-55d9ad",
    "mdi-run-55d9ad",
    "mdi-sport-shoe-55c9f0",
    "mdi-sparkles-55d9ad",
    "mdi-waves-55c9f0",
)

_TIME_OF_DAY_PREFIXES = {
    "morning", "morningm", "afternoon", "evening", "night", "dawn", "dusk",
    "am", "pm", "mañana", "tarde", "noche",
}


def _activity_display_name(name: str) -> str:
    words = name.split(maxsplit=1)
    if words and words[0].strip(".,:;()[]").casefold() in _TIME_OF_DAY_PREFIXES:
        return words[1].lstrip(" -:,.;") if len(words) > 1 else "Entrenamiento"
    return name

def _activity_group(activity_type: str) -> str:
    normalized_type = activity_type.casefold()
    if any(keyword in normalized_type for keyword in ("run", "running")):
        return "Running"
    if any(keyword in normalized_type for keyword in ("cycl", "cicl", "ride", "bike", "bici", "mtb")):
        return "Cycling"
    if any(keyword in normalized_type for keyword in ("weight", "strength", "fuerza")):
        return "Weight Training"
    if "yoga" in normalized_type:
        return "Yoga"
    return activity_type


def _activity_icon(activity_type: str) -> str:
    group = _activity_group(activity_type)
    icons = {
        "Running": ("sport-shoe", "#55c9f0"),
        "Cycling": ("bike", "#f2c45d"),
        "Weight Training": ("dumbbell", "#f2c45d"),
        "Yoga": ("lotus", "#c49bff"),
        "Swimming": ("waves", "#55c9f0"),
    }
    name, color = icons.get(group, ("activity", "#63d8ad"))
    return _line_icon(name, color, 18)


def _line_icon(name: str, color: str, size: int = 18) -> str:
    content_id = f"mdi-{name}-{color.lstrip('#').lower()}"
    return (
        f"<img data-icon='{name}' src='cid:{content_id}' width='{size}' height='{size}' "
        "alt='' style='display:inline-block;vertical-align:middle;border:0'>"
    )


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _display_number(value, decimals: int = 0) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if decimals == 0:
        return f"{number:,.0f}"
    return f"{number:,.{decimals}f}"


def _localized_number(value, decimals: int = 0) -> str:
    return _display_number(value, decimals).translate(str.maketrans(".,", ",."))


def _format_duration(seconds) -> str:
    duration = _number(seconds)
    if duration is None:
        return "—"
    minutes = max(0, int(duration // 60))
    hours, remainder = divmod(minutes, 60)
    return f"{hours} h {remainder:02d} min" if hours else f"{remainder} min"


def _compact_duration(seconds) -> str:
    duration = _number(seconds)
    if duration is None:
        return "—"
    minutes = max(0, int(duration // 60))
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h{remainder:02d}m" if hours else f"{remainder}m"


def _activity_day(activity: dict) -> str:
    return str(activity.get("activity_date") or "")[:10]


def _activity_time(activity: dict) -> str:
    raw_date = str(activity.get("activity_date") or "")
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return "Hora no disponible"


def summarize_activities(activities: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for activity in activities:
            activity_type = str(activity.get("activity_type") or "Otra actividad")
            by_type[_activity_group(activity_type)].append(activity)

    def sum_values(rows: list[dict], key: str) -> float | None:
        values = [_number(row.get(key)) for row in rows]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    type_summaries = []
    for activity_type, rows in sorted(by_type.items(), key=lambda item: (-len(item[1]), item[0])):
        distance_meters = sum_values(rows, "distance_meters")
        elevation_meters = sum_values(rows, "elevation_gain_meters")
        calories = sum_values(rows, "calories")
        type_summaries.append({
            "activity_type": activity_type,
            "sessions": len(rows),
            "duration_seconds": sum_values(rows, "moving_time_seconds"),
            "distance_meters": distance_meters if distance_meters else None,
            "elevation_meters": elevation_meters if elevation_meters else None,
            "calories": calories,
            "training_load": sum_values(rows, "icu_load"),
        })

    return {
        "sessions": len(activities),
        "duration_seconds": sum_values(activities, "moving_time_seconds") or 0,
        "distance_meters": sum_values(activities, "distance_meters"),
        "elevation_meters": sum_values(activities, "elevation_gain_meters"),
        "calories": sum_values(activities, "calories"),
        "training_load": sum_values(activities, "icu_load"),
        "by_type": type_summaries,
    }


def build_weekly_recap_email(
    week_start: date,
    week_end: date,
    activities: list[dict],
    summary: dict,
    narrative: dict[str, str],
    daily_wellness: dict[str, dict] | None = None,
    weekly_trend: list[dict] | None = None,
) -> tuple[str, str]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for activity in activities:
        by_day[_activity_day(activity)].append(activity)
    for day_activities in by_day.values():
        day_activities.sort(key=_activity_time)

    text_days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        day_key = day.isoformat()
        heading = _WEEKDAYS[offset]
        wellness = (daily_wellness or {}).get(day_key, {})
        steps_text = _display_number(wellness.get("steps"))
        weight_text = _display_number(wellness.get("weight_kg"), 1)
        resting_hr_text = _display_number(wellness.get("resting_hr"))
        sleep_text = _format_duration(wellness.get("sleep_seconds"))
        sleep_score_text = _display_number(wellness.get("sleep_score"))
        avg_sleep_hr_text = _display_number(wellness.get("avg_sleep_hr"))
        activities_text = []
        for activity in by_day.get(day_key, []):
            name = str(activity.get("activity_name") or "Entrenamiento")
            display_name = _activity_display_name(name)
            feel = activity.get("feel")
            feel_text = f"{_display_number(feel)}/5" if feel is not None else "—"
            rpe_text = f"{_display_number(activity.get('rpe'))}/10" if activity.get("rpe") is not None else "—"
            load_text = _display_number(activity.get("icu_load"))
            heart_rate = activity.get("average_hr")
            heart_rate_text = _display_number(heart_rate) if heart_rate is not None else "—"
            activities_text.append(
                f"{_activity_time(activity)} · {display_name} · sensación {feel_text} · "
                f"RPE {rpe_text} · carga {load_text} · FC media {heart_rate_text}"
            )

        if not activities_text:
            activities_text.append("Sin entrenamientos sincronizados")

        text_days.append(
            f"{heading}\n" + "\n".join(activities_text)
            + f"\nSalud: pasos {steps_text} · peso {weight_text} kg · FC en reposo {resting_hr_text} bpm · sueño {sleep_text} · puntuación {sleep_score_text} · FC media durante el sueño {avg_sleep_hr_text} bpm"
        )

    type_rows_text = []
    for item in summary["by_type"]:
        distance = item["distance_meters"]
        elevation = item["elevation_meters"]
        load = item["training_load"]
        duration_text = _compact_duration(item["duration_seconds"])
        distance_text = f"{_display_number(distance / 1000, 1)} km" if distance else "—"
        elevation_text = f"{_display_number(elevation)} m" if elevation else "—"
        load_text = _display_number(load)
        type_rows_text.append(
            f"{item['activity_type']}: {item['sessions']} sesiones, "
            f"{_format_duration(item['duration_seconds'])}, distancia {distance_text}, "
            f"desnivel {elevation_text}, carga {load_text}"
        )
    if not type_rows_text:
        type_rows_text.append("Sin actividad sincronizada")

    summary_line = str(narrative.get("summary") or "No hay datos suficientes para redactar un resumen.")
    summary_line = _SUMMARY_HEADING_RE.sub("", summary_line, count=1).strip()
    if not summary_line:
        summary_line = "No hay datos suficientes para redactar un resumen."
    summary_heading = " ".join(
        str(narrative.get("summary_heading") or "Tu semana de entrenamiento").split()
    )

    def narrative_items(field: str, limit: int) -> list[dict[str, str]]:
        raw_items = narrative.get(field)
        if not isinstance(raw_items, list):
            return []
        items = []
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            detail = str(item.get("detail") or "").strip()
            if not title or not detail:
                continue
            normalized = {"title": title, "detail": detail}
            if field == "recommendations":
                normalized["category"] = str(item.get("category") or "ACCIÓN").strip()
            items.append(normalized)
        return items

    key_findings = narrative_items("key_findings", 3)
    if not key_findings:
        key_findings = [{
            "title": "Sin hallazgos destacados",
            "detail": "No hay datos suficientes para señalar un cambio relevante esta semana.",
        }]
    recommendations = narrative_items("recommendations", 4)
    if not recommendations:
        recommendations = [{
            "category": "GENERAL",
            "title": "Mantener una progresión prudente",
            "detail": "No hay datos suficientes para proponer acciones más específicas.",
        }]

    def recommendation_icon(category: str, size: int) -> str:
        normalized = category.casefold()
        if any(word in normalized for word in ("sueño", "recuper", "descanso")):
            name = "moon"
        if any(word in normalized for word in ("entren", "carga", "sesion")):
            name = "run"
        elif any(word in normalized for word in ("vigilar", "pulso", "salud")):
            name = "heart"
        elif not any(word in normalized for word in ("sueño", "recuper", "descanso")):
            name = "sparkles"
        return _line_icon(name, "#55d9ad", size)

    finding_blocks = "".join(
        "<div style='padding:9px 0;border-bottom:1px solid #293542'>"
        f"<div style='color:#f0f3f7;font-size:14px;font-weight:bold;line-height:1.3'>{escape(item['title'])}</div>"
        f"<div style='padding-top:5px;color:#c3d0d4;font-size:12px;line-height:1.5'>{escape(item['detail'])}</div>"
        "</div>"
        for item in key_findings
    )
    recommendation_blocks = []
    for index, item in enumerate(recommendations):
        if index == 0:
            recommendation_blocks.append(
                "<div style='margin:5px 0 10px;padding:14px;background:#1b3331;border-radius:8px'>"
                f"<div style='color:#55d9ad;font-size:11px;line-height:1.4'>{recommendation_icon(item['category'], 18)} "
                f"<span style='font-size:11px;font-weight:bold;vertical-align:3px'>{escape(item['category'].upper())}</span></div>"
                f"<div style='padding-top:7px;color:#f3f7f5;font-size:15px;font-weight:bold;line-height:1.3'>{escape(item['title'])}</div>"
                f"<div style='padding-top:5px;color:#d5e0e3;font-size:13px;line-height:1.5'>{escape(item['detail'])}</div>"
                "</div>"
            )
        else:
            recommendation_blocks.append(
                "<div style='padding:9px 0'>"
                f"<div style='color:#a6b2c3;font-size:11px;line-height:1.4'>{recommendation_icon(item['category'], 16)} "
                f"<span style='font-size:11px;font-weight:bold;vertical-align:3px'>{escape(item['category'].upper())}</span></div>"
                f"<div style='padding-top:4px;color:#f0f3f7;font-size:14px;font-weight:bold;line-height:1.3'>{escape(item['title'])}</div>"
                f"<div style='padding-top:4px;color:#c3d0d4;font-size:12px;line-height:1.5'>{escape(item['detail'])}</div>"
                "</div>"
            )
    recommendation_blocks_html = "".join(recommendation_blocks)
    findings_text = "\n".join(
        f"- {item['title']}: {item['detail']}" for item in key_findings
    )
    recommendations_text = "\n".join(
        f"{'★' if index == 0 else '-'} {item['category'].upper()} · {item['title']}: {item['detail']}"
        for index, item in enumerate(recommendations)
    )
    total_load = _display_number(summary.get("training_load"))
    total_hours = _compact_duration(summary.get("duration_seconds"))
    total_distance = summary.get("distance_meters")
    distance_stat = f"{_display_number(total_distance / 1000, 1)} km" if total_distance else "—"
    total_elevation = summary.get("elevation_meters")
    trend_by_week = {str(row.get("week_start")): row for row in (weekly_trend or [])}
    current_trend = trend_by_week.get(week_start.isoformat(), {})
    latest_activity = sorted(activities, key=_activity_day, reverse=True)

    def latest_metric(field: str):
        return next((activity.get(field) for activity in latest_activity if activity.get(field) is not None), None)

    fitness = current_trend.get("last_ctl")
    if fitness is None:
        fitness = latest_metric("fitness_ctl")
    form = current_trend.get("last_tsb")
    if form is None:
        form = latest_metric("form_tsb")

    activity_columns = summary["by_type"]

    months = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")
    date_range = (
        f"{week_start.day:02d} {months[week_start.month - 1]} – "
        f"{week_end.day:02d} {months[week_end.month - 1]} · Semana {week_start.isocalendar().week}"
    )
    total_distance_text = (
        f"{_localized_number(total_distance / 1000, 1)} km"
        if total_distance else "—"
    )
    elevation_text = f"{_localized_number(total_elevation)} m" if total_elevation else "—"

    def metric_cell(value: str, caption: str, color: str, icon: str) -> str:
        return (
            "<td width='50%' valign='top' style='padding:4px'>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            "bgcolor='#171f2b' style='border-collapse:separate;border-radius:8px'>"
            f"<tr><td style='padding:11px 12px 10px'>"
            f"<div style='color:{color};font-size:18px;line-height:1'>{_line_icon(icon, color, 20)}</div>"
            f"<div style='padding-top:6px;color:#f2f5f8;font-size:21px;font-weight:bold;line-height:1.2'>{escape(value)}</div>"
            f"<div style='padding-top:4px;color:#a6b2c3;font-size:11px;line-height:1.3'>{escape(caption)}</div>"
            "</td></tr></table></td>"
        )

    metric_rows = (
        "<tr>"
        + metric_cell(_format_duration(summary.get("duration_seconds")), "Tiempo total", "#55c9f0", "clock")
        + metric_cell(total_distance_text, "Distancia · todas las actividades", "#55c9f0", "sport-shoe")
        + "</tr><tr>"
        + metric_cell(total_load, "Carga semanal", "#f4c65e", "chart")
        + metric_cell(elevation_text, "Desnivel · todas las actividades", "#c49bff", "mountain")
        + "</tr>"
    )

    group_colors = {
        "Running": "#54c9f1",
        "Cycling": "#f2c45d",
        "Weight Training": "#f2c45d",
        "Yoga": "#c29cff",
    }
    group_labels = {"Weight Training": "Fuerza", "Cycling": "Ciclismo"}
    max_group_load = max((_number(item.get("training_load")) or 0 for item in activity_columns), default=0)
    activity_rows = []
    for item in activity_columns:
        group = str(item["activity_type"])
        group_label = group_labels.get(group, group)
        group_color = group_colors.get(group, "#63d8ad")
        group_load = _number(item.get("training_load")) or 0
        bar_width = min(100, round(group_load / max_group_load * 100)) if max_group_load else 0
        detail = []
        if item.get("distance_meters"):
            detail.append(f"{_localized_number(item['distance_meters'] / 1000, 1)} km")
        if item.get("duration_seconds"):
            detail.append(_format_duration(item["duration_seconds"]))
        if item.get("training_load") is not None:
            detail.append(f"Carga {_localized_number(item['training_load'])}")
        icon = _activity_icon(group)
        activity_rows.append(
            "<tr><td style='padding:8px 0 3px'>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td width='42' valign='top' style='padding-right:10px'><table role='presentation' cellspacing='0' cellpadding='0' bgcolor='#243344' style='border-radius:6px'><tr><td width='36' height='36' align='center' style='font-size:18px'>{icon}</td></tr></table></td>"
            f"<td valign='top'><span style='color:#f0f3f7;font-size:13px;font-weight:bold'>{escape(group_label)}</span>"
            f"<br><span style='color:#a0adbf;font-size:11px;line-height:1.4'>{escape(' · '.join(detail) or 'Sin métricas disponibles')}</span>"
            f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='margin-top:5px'><tr>"
            f"<td width='{bar_width}%' height='4' bgcolor='{group_color}' style='height:4px;font-size:1px;line-height:1px'>&nbsp;</td>"
            f"<td width='{100 - bar_width}%' height='4' bgcolor='#303b4b' style='height:4px;font-size:1px;line-height:1px'>&nbsp;</td>"
            "</tr></table></td>"
            f"<td width='78' valign='top' align='right' style='padding-left:6px;color:#e5eaf0;font-size:12px'>{item['sessions']} {'sesión' if item['sessions'] == 1 else 'sesiones'}</td>"
            "</tr></table></td></tr>"
        )
    if not activity_rows:
        activity_rows.append(
            "<tr><td style='padding:14px 0;color:#a0adbf;font-size:13px'>Sin actividad sincronizada</td></tr>"
        )

    latest_sleep_day = next(
        (week_start + timedelta(days=offset) for offset in reversed(range(7))
         if any((daily_wellness or {}).get((week_start + timedelta(days=offset)).isoformat(), {}).get(field) is not None
                for field in ("sleep_seconds", "sleep_score"))),
        None,
    )
    latest_hr_day = next(
        (week_start + timedelta(days=offset) for offset in reversed(range(7))
         if (daily_wellness or {}).get((week_start + timedelta(days=offset)).isoformat(), {}).get("resting_hr") is not None),
        None,
    )
    latest_sleep_record = (daily_wellness or {}).get(latest_sleep_day.isoformat(), {}) if latest_sleep_day else {}
    latest_hr_record = (daily_wellness or {}).get(latest_hr_day.isoformat(), {}) if latest_hr_day else {}
    latest_day_label = _WEEKDAYS[latest_sleep_day.weekday()] if latest_sleep_day else "Sin datos"
    latest_sleep = _format_duration(latest_sleep_record.get("sleep_seconds"))
    latest_sleep_score = _localized_number(latest_sleep_record.get("sleep_score"))
    latest_resting_hr = _localized_number(latest_hr_record.get("resting_hr"))
    form_number = _number(form)
    form_text = _localized_number(form_number, 1)
    if form_number is not None and form_number > 0:
        form_text = f"+{form_text}"

    sleep_rows = []
    short_days = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        score = _number((daily_wellness or {}).get(day.isoformat(), {}).get("sleep_score"))
        score_text = _localized_number(score) if score is not None else "—"
        score_color = "#fb8588" if score is not None and score < 50 else "#f4c65e" if score is not None and score < 70 else "#55d9ad"
        bar_width = min(100, max(0, round(score))) if score is not None else 0
        sleep_rows.append(
            "<tr>"
            f"<td width='38' style='padding:6px;color:#a6b2c3;font-size:11px'>{short_days[offset]}</td>"
            "<td style='padding:4px 4px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td width='{bar_width}%' height='5' bgcolor='{score_color}' style='height:5px;font-size:1px;line-height:1px'>&nbsp;</td>"
            f"<td width='{100 - bar_width}%' height='5' bgcolor='#303b4b' style='height:5px;font-size:1px;line-height:1px'>&nbsp;</td>"
            "</tr></table></td>"
            f"<td width='34' align='right' style='padding:6px;color:#dfe6ee;font-size:11px'>{escape(score_text)}</td>"
            "</tr>"
        )

    html_content = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'></head>"
        "<body bgcolor='#07090c' style='margin:0;padding:0;background:#07090c;color:#edf2f7;font-family:Arial,Helvetica,sans-serif'>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#07090c'><tr><td align='center' style='padding:18px 10px'>"
        "<table role='presentation' width='560' cellspacing='0' cellpadding='0' bgcolor='#0d1219' style='width:100%;max-width:560px;border-collapse:separate;border-radius:12px'>"
        "<tr><td style='padding:20px 16px 10px'>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
        "<td style='color:#9aa8ba;font-size:11px'>TRAINING COACH · INFORME SEMANAL</td>"
        f"<td align='right' style='color:#55d9ad;font-size:14px'>{_line_icon('activity', '#55d9ad', 16)}</td></tr></table>"
        "<div style='padding-top:8px;color:#f1f4f8;font-size:24px;font-weight:bold;line-height:1.2'>Resumen de entrenamiento</div>"
        f"<div style='padding-top:4px;color:#9eacbf;font-size:12px'>{date_range}</div>"
        "</td></tr>"
        "<tr><td style='padding:0 16px 12px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#142b2b' style='border-radius:7px'><tr><td style='padding:13px'>"
        f"<div style='color:#55d9ad;font-size:11px;font-weight:bold;text-transform:uppercase'>{escape(summary_heading)}</div>"
        f"<div style='padding-top:6px;color:#d5e0e3;font-size:13px;line-height:1.5'>{escape(summary_line)}</div>"
        "</td></tr></table></td></tr>"
        f"<tr><td style='padding:0 10px 8px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>{metric_rows}</table></td></tr>"
        "<tr><td style='padding:8px 16px 2px;color:#f0f3f7;font-size:15px;font-weight:bold'>Actividad semanal</td></tr>"
        f"<tr><td style='padding:0 16px 12px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#171f2b' style='padding:0 11px;border-radius:8px'>{''.join(activity_rows)}</table></td></tr>"
        "<tr><td style='padding:8px 16px 6px;color:#f0f3f7;font-size:15px;font-weight:bold'>Recuperación</td></tr>"
        "<tr><td style='padding:0 16px 12px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#171f2b' style='border-radius:8px'><tr>"
        f"<td width='50%' valign='top' style='padding:13px 11px'><div style='color:#a6b2c3;font-size:11px'>Sueño · {escape(latest_day_label)}</div><div style='padding-top:5px;color:#fb8588;font-size:18px;font-weight:bold'>{escape(latest_sleep)}</div></td>"
        f"<td width='50%' align='right' valign='top' style='padding:13px 11px'><div style='color:#a6b2c3;font-size:11px'>Puntuación</div><div style='padding-top:5px;color:#fb8588;font-size:18px;font-weight:bold'>{escape(latest_sleep_score)}/100</div></td>"
        "</tr><tr>"
        f"<td valign='top' style='padding:7px 11px 11px'><div style='color:#a6b2c3;font-size:11px'>Pulso en reposo</div><div style='padding-top:5px;color:#ffbd65;font-size:17px;font-weight:bold'>{escape(latest_resting_hr)} bpm</div></td>"
        f"<td align='right' valign='top' style='padding:7px 11px 11px'><div style='color:#a6b2c3;font-size:11px'>Forma (TSB)</div><div style='padding-top:5px;color:#55d9ad;font-size:17px;font-weight:bold'>{escape(form_text)}</div></td>"
        "</tr><tr><td colspan='2' style='padding:0 11px 12px;color:#a6b2c3;font-size:11px;line-height:1.5'>El sueño y el pulso en reposo ayudan a contextualizar la carga semanal.</td></tr></table></td></tr>"
        "<tr><td style='padding:8px 16px 6px;color:#f0f3f7;font-size:15px;font-weight:bold'>Evolución de la semana</td></tr>"
        f"<tr><td style='padding:0 16px 12px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#171f2b' style='border-radius:8px'>{''.join(sleep_rows)}"
        "<tr><td colspan='3' style='padding:8px;color:#9eacbf;font-size:11px'>Puntuación del sueño · escala de 0 a 100</td></tr></table></td></tr>"
        "<tr><td style='padding:8px 16px 6px;color:#f0f3f7;font-size:15px;font-weight:bold'>Lo más importante</td></tr>"
        f"<tr><td style='padding:0 16px 10px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#171f2b' style='padding:3px 13px;border-radius:8px'><tr><td>{finding_blocks}</td></tr></table></td></tr>"
        "<tr><td style='padding:8px 16px 6px;color:#f0f3f7;font-size:15px;font-weight:bold'>Recomendaciones para la próxima semana</td></tr>"
        f"<tr><td style='padding:0 16px 14px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr><td>{recommendation_blocks_html}</td></tr></table></td></tr>"
        f"<tr><td style='padding:0 16px 16px;color:#77869a;font-size:10px'>TRAINING COACH · Semana {week_start.isocalendar().week} · Entrenamiento y recuperación</td></tr>"
        "</table></td></tr></table></body></html>"
    )

    text_content = (
        f"RECAP SEMANAL · {week_start.isoformat()} – {week_end.isoformat()}\n\n"
        f"RESUMEN DE LA SEMANA\n{summary_line}\n"
        f"{summary['sessions']} sesiones · {total_hours} · carga total {total_load} · distancia {distance_stat}\n\n"
        f"ENTRENAMIENTOS POR TIPO\n" + "\n".join(type_rows_text) + "\n\n"
        f"DIARIO DE ENTRENAMIENTOS\n" + "\n\n".join(text_days) + "\n\n"
        f"LO MÁS IMPORTANTE\n{findings_text}\n\n"
        f"RECOMENDACIONES PARA LA PRÓXIMA SEMANA\n{recommendations_text}"
    )
    return html_content, text_content