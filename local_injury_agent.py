from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from local_agent_memory import LocalAgentMemory

# 1. Define the State: This dictionary represents the memory/data shared between nodes
class AgentState(TypedDict):
    user_input: str
    latest_metrics: Dict[str, Any]
    injury_context: List[Dict[str, Any]]
    evaluation_risk: str
    final_prescription: str

# Initialize our ultra-fast offline memory
db_memory = LocalAgentMemory()

# 2. Define Node 1: Data Retrieval
def retrieve_athlete_data_node(state: AgentState) -> Dict[str, Any]:
    print("\n[NODE] -> Retrieving data from portable ecosystem...")
    
    # Get physical state from Intervals mock
    metrics = db_memory.get_latest_metrics()
    
    # Get qualitative historical text matching user concerns
    context = db_memory.get_injury_context(state["user_input"])
    
    return {
        "latest_metrics": metrics,
        "injury_context": context
    }

# 3. Define Node 2: Risk Evaluation (Rules Engine / AI Brain)
def evaluate_injury_risk_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE] -> Evaluating training load and physiological risks...")
    metrics = state["latest_metrics"]
    context = state["injury_context"]
    
    risk_report = "LOW_RISK"
    reasons = []
    
    # Rule A: Check Form (TSB)
    if metrics and metrics.get("form_tsb", 0) < -15.0:
        reasons.append(f"High Fatigue Alert: Form (TSB) is critically low ({metrics['form_tsb']}).")
        risk_report = "HIGH_RISK"
        
    # Rule B: Check Semantic Context from historical logs
    if context:
        reasons.append(f"Active Historical Context Found: User has past logs relating to this specific issue.")
        risk_report = "HIGH_RISK" if risk_report == "HIGH_RISK" else "MEDIUM_RISK"
        
    evaluation_summary = f"Status: {risk_report} | Issues detected: {', '.join(reasons) if reasons else 'None'}"
    print(f"       [Result] -> {evaluation_summary}")
    
    return {"evaluation_risk": risk_report}

# 4. Define Node 3: AI Output Prescription (Simulated LLM response for offline work)
def generate_prescription_node(state: AgentState) -> Dict[str, Any]:
    print("[NODE] -> Compiling adaptive training prescription...")
    risk = state["evaluation_risk"]
    metrics = state["latest_metrics"]
    
    # This acts as a placeholder for where the actual LLM prompt call will execute
    if risk == "HIGH_RISK":
        prescription = (
            f"⚠️ COACH ALERT: Training modifications REQUIRED.\n"
            f"Based on your latest activity '{metrics['activity_name']}', your Form (TSB) is at {metrics['form_tsb']} "
            f"indicating deep fatigue. Crucially, your logs indicate historical issues matching your current concern.\n"
            f"👉 PRESCRIPTION: Cancel high-impact workouts or downhills for the next 48 hours. Focus on zero-impact recovery (e.g., light cycling) or active mobility."
        )
    elif risk == "MEDIUM_RISK":
        prescription = "⚠️ COACH NOTICE: Proceed with caution. Monitor symptoms closely during warm-up. Keep intensity below threshold."
    else:
        prescription = "✅ COACH APPROVAL: Metrics and logs look clear. You are greenlit to proceed with your planned training session."
        
    return {"final_prescription": prescription}

# 5. Assemble the LangGraph Workflow Architecture
workflow = StateGraph(AgentState)

# Add our modular processing units (Nodes)
workflow.add_node("DataRetriever", retrieve_athlete_data_node)
workflow.add_node("RiskEvaluator", evaluate_injury_risk_node)
workflow.add_node("PrescriptionGenerator", generate_prescription_node)

# Set the execution flow connections (Edges)
workflow.set_entry_point("DataRetriever")
workflow.add_edge("DataRetriever", "RiskEvaluator")
workflow.add_edge("RiskEvaluator", "PrescriptionGenerator")
workflow.add_edge("PrescriptionGenerator", END)

# Compile the execution graph
coach_agent = workflow.compile()

# --- LOCAL PIPELINE EXECUTION TEST ---
if __name__ == "__main__":
    # Test case: The user is worried about their foot swelling again up north
    user_query = "I am feeling a bit of inflammation and soreness in my lower foot today, can I run?"
    
    print(f"🚀 Launching Coach Agent Graph for query: '{user_query}'")
    
    initial_state = {
        "user_input": user_query,
        "latest_metrics": {},
        "injury_context": [],
        "evaluation_risk": "",
        "final_prescription": ""
    }
    
    # Run the graph synchronously
    final_output = coach_agent.invoke(initial_state)
    
    print("\n" + "="*60)
    print("🤖 FINAL AGENT ADAPTIVE COACHING OUTPUT:")
    print("="*60)
    print(final_output["final_prescription"])
    print("="*60)