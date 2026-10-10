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
        "Running": "&#x1F3C3;&#xfe0f;",
        "Cycling": "&#x1F6B4;&#xfe0f;",
        "Weight Training": "&#x1F3CB;&#xfe0f;",
        "Yoga": "&#x1F9D8;&#xfe0f;",
        "Swimming": "&#x1F3CA;&#xfe0f;",
    }
    return icons.get(group, "&#x25cf;&#xfe0f;")


_RUN_OR_CYCLE_TYPES = ("run", "running", "cicl", "cycl", "ride", "bike", "bici", "mtb")


def _is_run_or_cycle(activity_type: str) -> bool:
    normalized_type = activity_type.casefold()
    return any(keyword in normalized_type for keyword in _RUN_OR_CYCLE_TYPES)


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


def _metric_text(value) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return _display_number(number) if number.is_integer() else _display_number(number, 1)


def _daily_metric_chart(
    title: str,
    field: str,
    daily_wellness: dict[str, dict],
    week_start: date,
    color: str,
    formatter,
    scale_max: float | None = None,
) -> str:
    days = [week_start + timedelta(days=offset) for offset in range(7)]
    values = [_number((daily_wellness.get(day.isoformat()) or {}).get(field)) for day in days]
    available = [value for value in values if value is not None and value > 0]
    maximum = scale_max or max(available, default=1)
    rows = []
    for day, value in zip(days, values):
        label = _WEEKDAYS[day.weekday()][:3]
        if value is None:
            bar_width = 0
            value_text = "—"
        else:
            bar_width = min(100, round(max(0, value) / maximum * 100))
            if value > 0:
                bar_width = max(4, bar_width)
            value_text = formatter(value)
        remainder = 100 - bar_width
        rows.append(
            "<tr>"
            f"<td width='34' style='padding:3px 6px;color:#aab4c2;font-size:12px'>{escape(label)}</td>"
            "<td style='padding:3px 4px'>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
            "<tr>"
            f"<td width='{bar_width}%' height='9' bgcolor='{color}' style='height:9px;font-size:1px;line-height:1px'>&nbsp;</td>"
            f"<td width='{remainder}%' height='9' bgcolor='#394251' style='height:9px;font-size:1px;line-height:1px'>&nbsp;</td>"
            "</tr></table></td>"
            f"<td width='78' align='right' style='padding:3px 6px;color:#e3e8ef;font-size:12px'>{escape(value_text)}</td>"
            "</tr>"
        )
    return (
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;margin:8px 0 16px'>"
        f"<tr><td colspan='3' style='padding:5px 6px;font-weight:bold;color:#e3e8ef'>{escape(title)}</td></tr>"
        f"{''.join(rows)}</table>"
    )


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

    html_days = []
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
        wellness_values = (
            ("sleep_seconds", "&#x1F319;&#xfe0f;", "Sueño", _compact_duration(wellness.get("sleep_seconds"))),
            ("sleep_score", "&#x2728;&#xfe0f;", "Puntuación del sueño", sleep_score_text),
            ("resting_hr", "&#x2764;&#xfe0f;", "FC en reposo", resting_hr_text),
            ("avg_sleep_hr", "&#x1F493;&#xfe0f;", "FC durante el sueño", avg_sleep_hr_text),
            ("weight_kg", "&#x2696;&#xfe0f;", "Peso", f"{weight_text} kg"),
            ("steps", "&#x1F463;&#xfe0f;", "Steps", steps_text),
        )
        wellness_cells = []
        for field, icon, label, value in wellness_values:
            if wellness.get(field) is None:
                metric = "&nbsp;"
            else:
                metric = (
                    f"<span title='{label}' aria-label='{label}: {escape(value)}' "
                    f"style='white-space:nowrap'><span>{icon}</span> "
                    f"<b style='color:#c6cfda'>{escape(value)}</b></span>"
                )
            wellness_cells.append(
                f"<td width='16.66%' valign='top' style='padding:2px 1px;color:#c6cfda;font-size:9px;white-space:nowrap'>{metric}</td>"
            )
        wellness_metrics_html = (
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            "style='width:100%;table-layout:fixed;border-collapse:collapse'><tr>"
            f"{''.join(wellness_cells)}</tr></table>"
        )
        activities_html = []
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
            duration_text = _compact_duration(activity.get("moving_time_seconds"))
            distance_text = ""
            if _is_run_or_cycle(str(activity.get("activity_type") or "")):
                distance = _number(activity.get("distance_meters"))
                if distance is not None and distance > 0:
                    distance_text = f"{_display_number(distance / 1000, 1)} km"
            activity_metrics = (
                (_activity_time(activity), "Hora", "width='9%'"),
                (escape(display_name), "Entrenamiento", "width='31%'"),
                (escape(duration_text), "Tiempo", "width='15%'"),
                (f"&#9889;&#xfe0f; {load_text}", "Load", "width='10%'"),
                (f"&#x2764;&#xfe0f; {heart_rate_text}", "FC", "width='7%'"),
                (distance_text, "Distancia", "width='8%'"),
                (f"&#x1F642;&#xfe0f; {feel_text}", "Sensación", "width='10%'"),
                (f"RPE {rpe_text}", "RPE", "width='10%'"),
            )
            activity_cells = "".join(
                f"<td {width} title='{label}' style='padding:2px 1px;color:#cbd3dd;font-size:9px;line-height:1.2;vertical-align:top;overflow-wrap:anywhere'>{value or '&nbsp;'}</td>"
                for value, label, width in activity_metrics
            )
            activities_html.append(
                f"<tr>{activity_cells}</tr>"
            )
            activities_text.append(
                f"{_activity_time(activity)} · {display_name} · sensación {feel_text} · "
                f"RPE {rpe_text} · carga {load_text} · FC media {heart_rate_text}"
            )

        if not activities_html:
            activities_html.append(
                "<tr><td colspan='8' style='padding:5px 0;color:#8995a5;font-size:12px'>Sin entrenamientos sincronizados</td></tr>"
            )
            activities_text.append("Sin entrenamientos sincronizados")

        html_days.append(
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='width:100%;table-layout:fixed;border-collapse:collapse;margin:8px 0 2px'>"
            f"<tr><td width='18%' valign='top' style='padding:0 4px 2px 0;white-space:nowrap'><h3 style='margin:0;color:#f0f3f7;font-size:12px'>{escape(heading)}</h3></td>"
            f"<td width='82%' valign='top' style='padding:0'>{wellness_metrics_html}</td></tr></table>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            "style='width:100%;table-layout:fixed;border-collapse:collapse;font-size:10px'>"
            f"<tbody>{''.join(activities_html)}</tbody></table>"
        )
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
    trends = str(narrative.get("trends") or "No hay datos suficientes para comparar tendencias.")
    suggestions = str(narrative.get("next_week_suggestions") or "Mantén un seguimiento regular de la carga y la recuperación.")
    total_load = _display_number(summary.get("training_load"))
    total_hours = _compact_duration(summary.get("duration_seconds"))
    total_distance = summary.get("distance_meters")
    distance_stat = f"{_display_number(total_distance / 1000, 1)} km" if total_distance else "—"
    total_elevation = summary.get("elevation_meters")
    elevation_stat = f"{_metric_text(total_elevation)}m" if total_elevation else "—"
    calories_value = _number(summary.get("calories"))
    total_calories = f"{calories_value:.0f}" if calories_value is not None else "—"
    trend_by_week = {str(row.get("week_start")): row for row in (weekly_trend or [])}
    current_trend = trend_by_week.get(week_start.isoformat(), {})
    previous_weeks = sorted(day for day in trend_by_week if day < week_start.isoformat())
    previous_trend = trend_by_week.get(previous_weeks[-1], {}) if previous_weeks else {}
    latest_activity = sorted(activities, key=_activity_day, reverse=True)

    def latest_metric(field: str):
        return next((activity.get(field) for activity in latest_activity if activity.get(field) is not None), None)

    fitness = current_trend.get("last_ctl")
    if fitness is None:
        fitness = latest_metric("fitness_ctl")
    fatigue = current_trend.get("last_atl")
    if fatigue is None:
        fatigue = latest_metric("fatigue_atl")
    form = current_trend.get("last_tsb")
    if form is None:
        form = latest_metric("form_tsb")
    current_fitness = _number(fitness)
    previous_fitness = _number(previous_trend.get("last_ctl"))
    ramp = current_fitness - previous_fitness if current_fitness is not None and previous_fitness is not None else None

    activity_columns = summary["by_type"]

    summary_rows = (
        (("Total", total_hours, "#f2f4f7", ""), ("Distance", distance_stat, "#f2f4f7", ""), ("Fitness", _metric_text(fitness), "#4b9cff", "")),
        (("Load", total_load, "#f2f4f7", ""), ("Elevation", elevation_stat, "#f2f4f7", ""), ("Fatigue", _metric_text(fatigue), "#f06a9b", "")),
        (("Ramp", _metric_text(ramp), "#f2f4f7", ""), ("kcal", total_calories, "#f2f4f7", ""), ("Form", _metric_text(form), "#54c878", "")),
    )
    stats_rows_html = []
    for row_index, metrics in enumerate(summary_rows):
        cells = []
        for label, value, value_color, icon in metrics:
            label_html = f"<span style='display:block;color:#98a2b3;font-size:11px'>{escape(label)}</span>" if label else ""
            value_html = f"<b style='display:block;color:{value_color};font-size:16px;line-height:1.15'>{icon}{escape(value)}</b>" if value else ""
            cells.append(
                f"<td valign='top' style='padding:7px 8px;font-size:12px;white-space:nowrap'>{label_html}{value_html}</td>"
            )
        for item in activity_columns:
            activity_type = escape(str(item["activity_type"]))
            if row_index == 0:
                icon = _activity_icon(str(item["activity_type"]))
                values = (
                    f"<span style='font-size:21px;line-height:1'>{icon}</span> "
                    f"<b style='font-size:16px'>{item['sessions']}×</b> "
                    f"<span style='display:block;color:#98a2b3;font-size:9px'>Load</span>"
                    f"<b style='font-size:12px'>&#9889;&#xfe0f; {_display_number(item['training_load'])}</b>"
                )
            elif row_index == 1:
                values = (
                    "<span style='display:block;color:#98a2b3;font-size:9px'>Time</span>"
                    f"<b style='font-size:12px'>{_compact_duration(item['duration_seconds'])}</b>"
                )
            else:
                distance = item["distance_meters"]
                values = (
                    "<span style='display:block;color:#98a2b3;font-size:9px'>Distance</span>"
                    f"<b style='font-size:12px'>{_display_number(distance / 1000, 1)} km</b>"
                    if distance else "—"
                )
            cells.append(
                f"<td title='{activity_type}' aria-label='{activity_type}' style='padding:2px 7px;font-size:12px;white-space:nowrap;color:#cbd3dd'>"
                f"{values}</td>"
            )
        stats_rows_html.append("<tr>" + "".join(cells) + "</tr>")
    wellness_rows = daily_wellness or {}
    daily_charts = "".join((
        _daily_metric_chart("Sueño", "sleep_seconds", wellness_rows, week_start, "#397c8a", _format_duration),
        _daily_metric_chart("Puntuación del sueño", "sleep_score", wellness_rows, week_start, "#5b8f76", lambda value: _display_number(value), 100),
        _daily_metric_chart("Frecuencia cardiaca en reposo", "resting_hr", wellness_rows, week_start, "#bd6b54", lambda value: f"{_display_number(value)} bpm"),
        _daily_metric_chart("FC media durante el sueño", "avg_sleep_hr", wellness_rows, week_start, "#81729a", lambda value: f"{_display_number(value)} bpm"),
        _daily_metric_chart("Pasos", "steps", wellness_rows, week_start, "#c18a3c", _display_number),
    ))

    html_content = (
        "<!doctype html><html><body bgcolor='#11151d' style='margin:0;background:#11151d;color:#e9edf3;font-family:Arial,sans-serif'>"
        "<div style='width:100%;margin:0;padding:0;background:#11151d'>"
        "<div bgcolor='#11151d' style='background:#11151d;color:#e9edf3'>"
        "<div style='padding:8px'>"
        f"<p style='margin:0 0 4px;color:#8e9bab;font-size:10px;text-transform:uppercase'>{week_start.strftime('%d %b')} - {week_end.strftime('%d %b')}</p>"
        f"<h1 style='margin:0 0 8px;color:#f0f3f7;font-size:20px'>Semana {week_start.isocalendar().week}</h1>"
        f"<h2 style='font-size:16px;color:#e9edf3;margin:16px 0 6px'>{escape(summary_heading)}</h2>"
        f"<p style='color:#c3ccd7;font-size:13px;line-height:1.45;margin:0'>{escape(summary_line)}</p>"
        "</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' bgcolor='#11151d' style='width:100%;border-collapse:collapse;background:#11151d;margin:0 0 12px'>"
        f"{''.join(stats_rows_html)}</table>"
        "<div style='padding:0 8px 8px;color:#e9edf3'>"
        f"{''.join(html_days)}"
        "<h2 style='font-size:15px;color:#f0f3f7;margin:10px 0 4px'>Tendencias</h2>"
        f"<p style='color:#c3ccd7;font-size:12px;line-height:1.35;margin:0 0 6px'>{escape(trends)}</p>"
        "<h2 style='font-size:15px;color:#f0f3f7;margin:10px 0 4px'>Sugerencias para la próxima semana</h2>"
        f"<p style='color:#c3ccd7;font-size:12px;line-height:1.35;margin:0 0 6px'>{escape(suggestions)}</p>"
        "<h2 style='font-size:15px;color:#f0f3f7;margin:10px 0 4px'>Gráficos diarios</h2>"
        f"{daily_charts}"
        "</div></div></div></body></html>"
    )

    text_content = (
        f"RECAP SEMANAL · {week_start.isoformat()} – {week_end.isoformat()}\n\n"
        f"RESUMEN DE LA SEMANA\n{summary_line}\n"
        f"{summary['sessions']} sesiones · {total_hours} · carga total {total_load} · distancia {distance_stat}\n\n"
        f"ENTRENAMIENTOS POR TIPO\n" + "\n".join(type_rows_text) + "\n\n"
        f"DIARIO DE ENTRENAMIENTOS\n" + "\n\n".join(text_days) + "\n\n"
        f"TENDENCIAS\n{trends}\n\n"
        f"SUGERENCIAS PARA LA PRÓXIMA SEMANA\n{suggestions}"
    )
    return html_content, text_content