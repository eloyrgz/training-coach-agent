import os
import sys
import re
import argparse
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

from agent_memory import SupabaseAgentMemory
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

load_dotenv(override=True)

class AgentState(TypedDict):
    user_input: str
    medical_history: str
    latest_metrics: Dict[str, Any]
    injury_context: List[Dict[str, Any]]
    symptom_signals: Dict[str, Any]
    history_signals: Dict[str, Any]
    risk_reasons: List[str]
    evaluation_risk: str
    final_prescription: str

_DB_MEMORY: SupabaseAgentMemory | None = None


def _get_db_memory() -> SupabaseAgentMemory:
    global _DB_MEMORY
    if _DB_MEMORY is None:
        _DB_MEMORY = SupabaseAgentMemory()
    return _DB_MEMORY


def close_db_memory() -> None:
    global _DB_MEMORY
    if _DB_MEMORY is not None:
        _DB_MEMORY.close()
        _DB_MEMORY = None

def retrieve_athlete_data_node(state: AgentState) -> Dict[str, Any]:
    print("\n[NODE 1] -> Querying Supabase Production Tables...")
    db_memory = _get_db_memory()
    metrics = db_memory.get_latest_metrics()
    context = db_memory.get_injury_context(state["user_input"])
    background = db_memory.get_medical_background()
    med_history = "\n".join([e["text"] for e in background]) if background else ""
    return {"latest_metrics": metrics, "injury_context": context, "medical_history": med_history}


def _extract_symptom_signals(user_input: str) -> Dict[str, Any]:
    text = (user_input or "").lower()

    severity = None
    score_match = re.search(r"(\d{1,2})\s*/\s*10", text)
    if score_match:
        try:
            parsed = int(score_match.group(1))
            if 0 <= parsed <= 10:
                severity = parsed
        except ValueError:
            severity = None

    if severity is None:
        generic_match = re.search(r"\b(dolor|pain|molestia|intensidad|severity)\b[^\d]{0,16}(\d{1,2})\b", text)
        if generic_match:
            try:
                parsed = int(generic_match.group(2))
                if 0 <= parsed <= 10:
                    severity = parsed
            except ValueError:
                severity = None

    swelling = any(k in text for k in ["inflam", "hinch", "edema", "swollen", "swelling"])

    red_flags: List[str] = []
    red_flag_rules = [
        ("neurological symptoms", ["entumec", "hormigue", "adormec", "numb", "tingling"]),
        ("pain at rest or night", ["dolor en reposo", "pain at rest", "dolor nocturno", "night pain"]),
        ("cannot bear weight", ["no puedo apoyar", "cannot bear weight", "coje", "cojera", "limp"]),
        ("progressive worsening", ["empeora", "worsening", "cada dia peor", "worse each day"]),
    ]
    for label, keywords in red_flag_rules:
        if any(k in text for k in keywords):
            red_flags.append(label)

    return {
        "severity_0_10": severity,
        "swelling": swelling,
        "red_flags": red_flags,
    }


def _extract_medical_history_signals(medical_history: str) -> Dict[str, Any]:
    text = (medical_history or "").lower()
    if not text:
        return {
            "has_history": False,
            "relevant_prior_injury": False,
            "recent_surgery": False,
        }

    relevant_injury_terms = [
        "neuroma",
        "fractura por estres",
        "stress fracture",
        "tendin",
        "fascitis",
        "plantar",
        "aquiles",
        "achilles",
        "esguince",
        "sprain",
    ]

    surgery_terms = ["cirugia", "surgery", "operacion", "operation", "post-operatorio", "post-op"]

    return {
        "has_history": True,
        "relevant_prior_injury": any(term in text for term in relevant_injury_terms),
        "recent_surgery": any(term in text for term in surgery_terms),
    }

def evaluate_injury_risk_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE 2] -> Analyzing physiological metrics and fatigue...")
    metrics = state["latest_metrics"]
    context = state["injury_context"]

    signals = _extract_symptom_signals(state.get("user_input", ""))
    history_signals = _extract_medical_history_signals(state.get("medical_history", ""))
    reasons: List[str] = []
    score = 0

    # Load-based risk from TSB.
    if metrics and metrics.get("form_tsb") is not None:
        try:
            tsb_val = float(metrics["form_tsb"])
            if tsb_val < -25.0:
                score += 2
                reasons.append(f"Very high fatigue (TSB: {tsb_val})")
            elif tsb_val < -15.0:
                score += 1
                reasons.append(f"High fatigue (TSB: {tsb_val})")
        except ValueError:
            pass

    # Historical similarity increases recurrence probability.
    if context:
        score += 1
        reasons.append("Matching historical injury logs found")

    severity = signals.get("severity_0_10")
    if isinstance(severity, int):
        if severity >= 7:
            score += 2
            reasons.append(f"High symptom severity ({severity}/10)")
        elif severity >= 4:
            score += 1
            reasons.append(f"Moderate symptom severity ({severity}/10)")

    if signals.get("swelling"):
        score += 1
        reasons.append("Reported swelling/inflammation")

    red_flags = signals.get("red_flags", [])
    if red_flags:
        score += 3
        reasons.append(f"Red flags: {', '.join(red_flags)}")

    if history_signals.get("relevant_prior_injury"):
        score += 1
        reasons.append("Relevant prior injury in medical history")

    if history_signals.get("recent_surgery"):
        score += 1
        reasons.append("Recent surgery/procedure declared in medical history")

    if score >= 4:
        risk_report = "HIGH_RISK"
    elif score >= 2:
        risk_report = "MEDIUM_RISK"
    else:
        risk_report = "LOW_RISK"

    print(f"          [Result] -> {risk_report} (Due to: {', '.join(reasons) if reasons else 'None'})")
    return {
        "evaluation_risk": risk_report,
        "risk_reasons": reasons,
        "symptom_signals": signals,
        "history_signals": history_signals,
    }

def generate_prescription_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE 3] -> Requesting live prescription from OpenAI GPT...")
    
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.3)

    system_prompt = (
        "You are an elite endurance sports coach and sports physiotherapist.\n"
        "Analyze the athlete's query using their accurate data:\n\n"
        "--- ATHLETE DATA ---\n"
        "- Risk Category: {risk_level}\n"
        "- Risk Reasons: {risk_reasons}\n"
        "- Last Session: {act_name} ({act_type})\n"
        "- CTL (Fitness): {ctl} | ATL (Fatigue): {atl} | TSB (Form): {tsb}\n\n"
        "--- SYMPTOM SIGNALS ---\n"
        "- Severity (0-10): {symptom_severity}\n"
        "- Swelling: {swelling}\n"
        "- Red Flags: {red_flags}\n\n"
        "--- MEDICAL HISTORY ---\n"
        "{medical_history}\n\n"
        "--- HISTORICAL LOGS ---\n"
        "{historical_context}\n\n"
        "--- DIRECTIVES ---\n"
        "Provide technical, structured training advice in bullet points. Respond in Spanish. Be concise and highly specific to their metrics. "
        "If risk is HIGH_RISK or red flags exist, prioritize conservative guidance and explicit stop/seek-care criteria."
    )

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_query}")
    ])

    metrics = state["latest_metrics"] or {}
    signals = state.get("symptom_signals") or {}
    reasons = state.get("risk_reasons") or []
    context_str = "".join([f"- [{item['date']}]: {item['text']}\n" for item in state["injury_context"]]) if state["injury_context"] else "None."

    formatted_prompt = prompt_template.format_messages(
        risk_level=state["evaluation_risk"],
        risk_reasons=", ".join(reasons) if reasons else "None",
        act_name=metrics.get("activity_name", "N/A"),
        act_type=metrics.get("activity_type", "N/A"),
        ctl=metrics.get("fitness_ctl", "N/A"),
        atl=metrics.get("fatigue_atl", "N/A"),
        tsb=metrics.get("form_tsb", "N/A"),
        symptom_severity=signals.get("severity_0_10", "Unknown"),
        swelling="Yes" if signals.get("swelling") else "No",
        red_flags=", ".join(signals.get("red_flags", [])) if signals.get("red_flags") else "None",
        medical_history=state.get("medical_history") or "None provided.",
        historical_context=context_str,
        user_query=state["user_input"]
    )

    response = llm.invoke(formatted_prompt)
    return {"final_prescription": response.content}

# Workflow Construction
workflow = StateGraph(AgentState)
workflow.add_node("DataRetriever", retrieve_athlete_data_node)
workflow.add_node("RiskEvaluator", evaluate_injury_risk_node)
workflow.add_node("PrescriptionGenerator", generate_prescription_node)

workflow.set_entry_point("DataRetriever")
workflow.add_edge("DataRetriever", "RiskEvaluator")
workflow.add_edge("RiskEvaluator", "PrescriptionGenerator")
workflow.add_edge("PrescriptionGenerator", END)

coach_agent = workflow.compile()


def run_injury_assessment(user_query: str) -> Dict[str, Any]:
    initial_state: AgentState = {
        "user_input": user_query,
        "medical_history": "",
        "latest_metrics": {},
        "injury_context": [],
        "symptom_signals": {},
        "history_signals": {},
        "risk_reasons": [],
        "evaluation_risk": "",
        "final_prescription": "",
    }
    return coach_agent.invoke(initial_state)


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Injury risk assessment agent")
    parser.add_argument("query", nargs="*", help="Injury/symptom query text")
    return parser.parse_args()

if __name__ == "__main__":
    args = _parse_cli_args()

    if args.query:
        user_query = " ".join(args.query).strip()
    else:
        user_query = input(
            "Describe tu molestia y contexto (ej: dolor 6/10, inflamación, actividad que la disparó):\n> "
        ).strip()

    if not user_query:
        print("⚠️ No se recibió consulta. Saliendo.")
        close_db_memory()
        raise SystemExit(0)

    print(f"🚀 Launching Live Agent for query: '{user_query}'")

    final_output = run_injury_assessment(user_query)
    
    print("\n============================================================")
    print("🤖 LIVE AGENT ADAPTIVE COACHING OUTPUT:")
    print("============================================================")
    print(f"Risk: {final_output.get('evaluation_risk', 'N/A')}")
    print(f"Medical background loaded: {'Yes' if final_output.get('medical_history') else 'No'}")
    print(final_output["final_prescription"])
    print("============================================================")
    
    close_db_memory()

    