import os
import sys
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

from agent_memory import LocalAgentMemory
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

load_dotenv(override=True)

class AgentState(TypedDict):
    user_input: str
    latest_metrics: Dict[str, Any]
    injury_context: List[Dict[str, Any]]
    evaluation_risk: str
    final_prescription: str

# Inicializamos la memoria real conectada a Supabase
db_memory = LocalAgentMemory()

def retrieve_athlete_data_node(state: AgentState) -> Dict[str, Any]:
    print("\n[NODE 1] -> Querying Supabase Production Tables...")
    metrics = db_memory.get_latest_metrics()
    context = db_memory.get_injury_context(state["user_input"])
    return {"latest_metrics": metrics, "injury_context": context}

def evaluate_injury_risk_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE 2] -> Analyzing physiological metrics and fatigue...")
    metrics = state["latest_metrics"]
    context = state["injury_context"]
    
    risk_report = "LOW_RISK"
    reasons = []
    
    # Manejo seguro si form_tsb viene como None o no existe
    if metrics and metrics.get("form_tsb") is not None:
        try:
            tsb_val = float(metrics["form_tsb"])
            if tsb_val < -15.0:
                reasons.append(f"High Fatigue (TSB: {tsb_val})")
                risk_report = "HIGH_RISK"
        except ValueError:
            pass
        
    if context:
        reasons.append(f"Matching historical injury logs found")
        risk_report = "HIGH_RISK" if risk_report == "HIGH_RISK" else "MEDIUM_RISK"
        
    print(f"          [Result] -> {risk_report} (Due to: {', '.join(reasons) if reasons else 'None'})")
    return {"evaluation_risk": risk_report}

def generate_prescription_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE 3] -> Requesting live prescription from OpenAI GPT...")
    
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.3)

    system_prompt = (
        "You are an elite endurance sports coach and sports physiotherapist.\n"
        "Analyze the athlete's query using their accurate data:\n\n"
        "--- ATHLETE DATA ---\n"
        "- Risk Category: {risk_level}\n"
        "- Last Session: {act_name} ({act_type})\n"
        "- CTL (Fitness): {ctl} | ATL (Fatigue): {atl} | TSB (Form): {tsb}\n\n"
        "--- HISTORICAL LOGS ---\n"
        "{historical_context}\n\n"
        "--- DIRECTIVES ---\n"
        "Provide technical, structured training advice in bullet points. Respond in Spanish. Be concise and highly specific to their metrics."
    )

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_query}")
    ])

    metrics = state["latest_metrics"] or {}
    context_str = "".join([f"- [{item['date']}]: {item['text']}\n" for item in state["injury_context"]]) if state["injury_context"] else "None."

    formatted_prompt = prompt_template.format_messages(
        risk_level=state["evaluation_risk"],
        act_name=metrics.get("activity_name", "N/A"),
        act_type=metrics.get("activity_type", "N/A"),
        ctl=metrics.get("fitness_ctl", "N/A"),
        atl=metrics.get("fatigue_atl", "N/A"),
        tsb=metrics.get("form_tsb", "N/A"),
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

if __name__ == "__main__":
    user_query = "Me preocupa un pie, noto inflamación en la zona del neuroma esta semana, ¿debería descansar?"
    print(f"🚀 Launching Live Agent for query: '{user_query}'")
    
    initial_state = {"user_input": user_query, "latest_metrics": {}, "injury_context": [], "evaluation_risk": "", "final_prescription": ""}
    final_output = coach_agent.invoke(initial_state)
    
    print("\n============================================================")
    print("🤖 LIVE AGENT ADAPTIVE COACHING OUTPUT:")
    print("============================================================")
    print(final_output["final_prescription"])
    print("============================================================")
    
    db_memory.close()