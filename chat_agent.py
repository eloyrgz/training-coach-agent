import os
import json
import warnings
import time
from datetime import date, timedelta
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from openai import RateLimitError

from coach_tools import ALL_TOOLS, memory as db_memory
from plan_tools import PLAN_TOOLS
from ntc_tools import NTC_TOOLS

ALL_TOOLS = ALL_TOOLS + PLAN_TOOLS + NTC_TOOLS

warnings.filterwarnings("ignore", category=DeprecationWarning)
load_dotenv(override=True)

def _build_system_prompt() -> str:
    today = date.today()
    today_iso = today.isoformat()
    week_end = (today + timedelta(days=6)).isoformat()

    # ISO week: Monday = day 0, Sunday = day 6
    this_week_monday = (today - timedelta(days=today.weekday())).isoformat()
    this_week_sunday = (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat()
    last_week_monday = (today - timedelta(days=today.weekday() + 7)).isoformat()
    last_week_sunday = (today - timedelta(days=today.weekday() + 1)).isoformat()

    return f"""You are a personalized endurance sports coach with access to the athlete's training database.
Today's date is {today_iso}.

WEEK CONVENTION: weeks start on MONDAY (ISO/European standard, Spain).
- This week: {this_week_monday} → {this_week_sunday}
- Last week: {last_week_monday} → {last_week_sunday}
Always use these exact dates when the user says 'esta semana', 'la semana pasada', 'this week', 'last week'.

Guidelines:
- Use the most specific tool available. Call tools with precise parameters to get exactly the data needed.
- For 'top N' questions (longest, hardest, highest), use query_activities with sort_by and limit.
- Prefer aggregation tools (get_volume_summary, get_personal_bests) over raw activity queries when possible.
- Once you have the data, answer immediately without calling additional tools.
- ALWAYS infer activity_type from sport-specific words in the question:
  'carrera/run/running/correr' → activity_type='Run'
  'trail/trailrun/montaña' → activity_type='TrailRun'
  'bici de montaña/mtb/mountain bike' → activity_type='MountainBikeRide'
  'bici/ciclismo/ride/cycling' → activity_type='Ride'
  'natación/swim/nadar' → activity_type='Swim'
  'senderismo/hiking/hike' → activity_type='Hike'
  'caminar/walk/marcha' → activity_type='Walk'
  'strength/fuerza/weight' → activity_type='WeightTraining'
  'yoga' → activity_type='Yoga'
  'paddle/sup/paddleboarding' → activity_type='StandUpPaddling'
  'escalada/climbing' → activity_type='RockClimbing'
  If the question is sport-specific, NEVER query without activity_type filter.
- For PLANNED/FUTURE workouts use get_scheduled_workouts:
  'workout del día/qué tengo hoy/plan de hoy' → get_scheduled_workouts('{today_iso}', '{today_iso}')
  'plan de esta semana/próximos días' → get_scheduled_workouts('{today_iso}', '{week_end}')
  'entrenamientos programados/qué tengo planificado' → get_scheduled_workouts with appropriate dates
- For PLANNED vs ACTUAL comparison use compare_planned_vs_actual:
  'cómo fue vs el plan/cumplí el entrenamiento/planned vs actual' → compare_planned_vs_actual('{today_iso}')
  Always use this tool when the user asks whether they hit their planned targets.
- For logging RPE use log_rpe:
  'fue un 8 de esfuerzo/mi RPE fue 7/registra esfuerzo' → log_rpe(rpe=<value>, target_date='{today_iso}')
  RPE scale 1-10. Saves to both Intervals.icu and local DB simultaneously.
- For adding notes/sensations use add_training_note:
  'sentí dolor en la rodilla/me sentí bien/añade nota' → add_training_note(note=<text>, note_date='{today_iso}')
  Saves to local DB (searchable) AND posts as comment on the Intervals.icu activity.
- For TRAINING PLAN generation and management:
  'mis planes/planes generados' → list_plans
  'detalle del plan/resumen del plan' → get_plan_summary
  'sube el plan a Intervals/push plan' → push_plan_to_intervals
  'borra el plan/eliminar plan/no me gusta el plan' → delete_plan (removes from DB and Intervals.icu calendar)

  PLAN CREATION ASSISTANT — when the user wants to create/generate a training plan, follow this workflow:
  1. Call get_latest_metrics to get the athlete's current CTL (fitness level).
  2. Call list_blueprints to know the available templates.
  3. Ask the user the following questions ONE message at a time (group related ones together):
     a) Goal/race: "¿Para qué carrera o distancia es el plan?" (use this for the label and race_distance_km)
     b) Blueprint: Present the available blueprints briefly and ask which level fits them (L1=beginner, L2=intermediate).
     c) Weekly hours: "¿Cuántas horas semanales puedes dedicar al entrenamiento? (entre 3.5 y 12h)"
     d) Plan length: "¿Cuántas semanas tiene el plan? (10–22, por defecto 18)"
     e) Schedule preferences: "¿Qué día prefieres para la tirada larga? ¿Hay días que no puedas entrenar?"
     f) Session cap: "¿Quieres limitar el número máximo de sesiones por semana?" (optional, skip if user seems satisfied)
  4. Use sensible defaults for anything the user skips or says "lo que sea":
     - available_weeks=18, preferred_long_run_day="Sun", max_long_run_hours=4.0
     - current_ctl from step 1 (auto-populated, don't ask the user)
  5. Once you have enough info, call generate_training_plan with all parameters.
  6. Present a concise summary of the generated plan (phases, weekly hours, total workouts).
  7. Ask: "¿Quieres que suba el plan a tu calendario de Intervals.icu?" → if yes, call push_plan_to_intervals.
  If the user is unhappy with the plan or wants to start over, offer to delete it with delete_plan and regenerate.
  
  IMPORTANT: Do NOT ask all questions at once. Be conversational — adapt based on user responses.
  If the user provides multiple answers in one message, acknowledge them and move forward.
  If the user says something like "genera un plan rápido", use defaults and only confirm the essentials (blueprint + hours).

- For NIKE TRAINING CLUB (NTC) cross-training workouts:
  'busca entrenamientos de fuerza/yoga/movilidad' → search_ntc_workouts(focus=...)
  'recomienda un workout para hoy' → suggest_ntc_workout(goal=...)
  'programa ese workout para el martes' → schedule_ntc_workout(workout_uid=..., target_date=...)
  The NTC catalog has 724 workouts: strength, endurance, mobility, yoga.
  Filters: focus (strength/endurance/mobility), level (beginner/intermediate/advanced),
  equipment (none/basic/full), muscle_group (glutes/abs/arms), duration_max (minutes).
  On recovery days or when TSB is low, proactively suggest NTC Yoga/Mobility sessions.
  When the user picks a workout from search results, schedule it with schedule_ntc_workout.
  When ntc_app_link is present, include it as a markdown link: [Abrir en NTC](nike-ntc://workout/...)

Respond in Spanish unless the user writes in another language. Be concise and data-driven."""


SYSTEM_PROMPT = _build_system_prompt()

TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    temperature=0.3,
    max_retries=0,
).bind_tools(ALL_TOOLS)


def call_llm_with_retry(messages: list, max_retries: int = 4) -> AIMessage:
    wait = 5
    for attempt in range(max_retries):
        try:
            return llm.invoke(messages)
        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            print(f"\n⏳ Rate limit alcanzado. Reintentando en {wait}s...")
            time.sleep(wait)
            wait = min(wait * 2, 60)


def run_agent(conversation_messages: list, max_tool_calls: int = 6) -> str:
    messages = [SystemMessage(content=_build_system_prompt())] + conversation_messages

    for step in range(max_tool_calls + 1):
        response = call_llm_with_retry(messages)

        if not response.tool_calls:
            return response.content or "_(sin respuesta)_"

        messages.append(response)
        for tool_call in response.tool_calls:
            tool = TOOLS_BY_NAME.get(tool_call["name"])
            if tool is None:
                result = f"Error: unknown tool '{tool_call['name']}'"
            else:
                result = tool.invoke(tool_call["args"])
            messages.append(ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=tool_call["id"],
            ))

    # Exceeded max tool calls — ask LLM to answer with what it has
    messages.append(HumanMessage(content="Por favor responde con la información que ya tienes disponible."))
    response = call_llm_with_retry(messages)
    return response.content or "_(sin respuesta)_"


if __name__ == "__main__":
    print("🤖  Training Coach — Modo Conversacional")
    print("Escribe 'salir' o 'exit' para terminar.\n")

    conversation_messages = []

    try:
        while True:
            try:
                user_input = input("Tú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n¡Hasta luego!")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "salir"):
                print("¡Hasta luego!")
                break

            conversation_messages.append(HumanMessage(content=user_input))

            try:
                print("⏳ Pensando...", end="\r")
                reply = run_agent(conversation_messages)
                conversation_messages.append(AIMessage(content=reply))
                print(f"\nCoach: {reply}\n")
            except RateLimitError:
                conversation_messages.pop()
                print("\n⚠️  Límite de tokens superado. Espera un minuto e inténtalo de nuevo.\n")
    finally:
        db_memory.close()
