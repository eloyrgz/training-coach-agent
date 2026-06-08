# 🤖 Training Coach & Injury Prevention Agent

This project is an **Intelligent Endurance Training Agent** built using graph architectures (`LangGraph`) and vector databases (`Supabase + pgvector`). The system automatically synchronizes physiological metrics and training logs from **Intervals.icu**, computes text embeddings locally native to Linux, and evaluates acute fatigue spikes or injury risks utilizing OpenAI's LLMs.

## 🏗️ System Architecture

The pipeline is split into two independent core components:

1. **`sync_pipeline.py` (Data Ingestion & Embedding ETL):**
   * Connects to the Intervals.icu API and extracts recent activities (distance, time, CTL, ATL, TSB, RPE).
   * Downloads the Hugging Face `all-MiniLM-L6-v2` model locally via `sentence-transformers` to calculate vector embeddings for subjective workout descriptions.
   * Performs an `UPSERT` on quantitative metrics and updates the vectorized database in Supabase.
2. **`injury_agent.py` (LangGraph Orchestrator):**
   * Orchestrates a structured state graph workflow across three principal nodes:
     * **Node 1 (DataRetriever):** Executes a Cosine Similarity vector search in Supabase to retrieve relevant historical logs based on the user's current physical complaints.
     * **Node 2 (RiskEvaluator):** Assesses deterministic physiological boundaries (e.g., flagging high fatigue if $TSB < -15.0$).
     * **Node 3 (PrescriptionGenerator):** Feeds the consolidated physical metrics and matched historical context into `gpt-4o-mini` to deliver a highly technical training prescription.

---

## 📋 Prerequisites

* **OS:** Linux (Ubuntu/Debian/Fedora) with IPv4 network capabilities (or a configured connection pooler to bypass IPv6 translations).
* **Database:** A **Supabase** instance with the `pgvector` extension enabled.
* **Python:** Version 3.10 or higher (Virtual environment highly recommended).

---

## ⚙️ Environment Variables Config (`.env`)

Create a `.env` file in the root directory of your project using the following layout (ensure it is clean of duplicate physical quotation marks or trailing spaces):

```env
# --- INTERVALS.ICU CONFIG ---
# Athlete ID (Include the 'i' prefix if required by your API_KEY authorization type)
INTERVALS_ATHLETE_ID=i87571
INTERVALS_API_KEY=your_25_character_api_key

# --- SUPABASE DATABASE CONFIG ---
# Use the Pooler host (IPv4) if your local ISP network has strict IPv6 routing constraints
SUPABASE_DB_URI=postgresql://postgres.xxxx:password@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
SUPABASE_POOLER_DB_URI=postgresql://postgres.xxxx:password@aws-0-eu-west-1.pooler.supabase.com:6543/postgres

# --- OPENAI API CONFIG ---
OPENAI_API_KEY=sk-proj-your_openai_api_key
OPENAI_MODEL=gpt-4o-mini