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

warnings.filterwarnings("ignore", category=DeprecationWarning)
load_dotenv(override=True)

def _build_system_prompt() -> str:
    today = date.today().isoformat()
    week_end = (date.today() + timedelta(days=6)).isoformat()
    return f"""You are a personalized endurance sports coach with access to the athlete's training database.
Today's date is {today}.

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
  'workout del día/qué tengo hoy/plan de hoy' → get_scheduled_workouts('{today}', '{today}')
  'plan de esta semana/próximos días' → get_scheduled_workouts('{today}', '{week_end}')
  'entrenamientos programados/qué tengo planificado' → get_scheduled_workouts with appropriate dates
- For PLANNED vs ACTUAL comparison use compare_planned_vs_actual:
  'cómo fue vs el plan/cumplí el entrenamiento/planned vs actual' → compare_planned_vs_actual('{today}')
  Always use this tool when the user asks whether they hit their planned targets.
- For logging RPE use log_rpe:
  'fue un 8 de esfuerzo/mi RPE fue 7/registra esfuerzo' → log_rpe(rpe=<value>, target_date='{today}')
  RPE scale 1-10. Saves to both Intervals.icu and local DB simultaneously.
- For adding notes/sensations use add_training_note:
  'sentí dolor en la rodilla/me sentí bien/añade nota' → add_training_note(note=<text>, note_date='{today}')
  Saves to local DB (searchable) AND posts as comment on the Intervals.icu activity.

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
