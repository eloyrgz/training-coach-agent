# 🤖 Training Coach & Injury Prevention Agent

This project is an **Intelligent Endurance Training Agent** built using graph architectures (`LangGraph`) and vector databases (`Supabase + pgvector`). The system automatically synchronizes physiological metrics and training logs from **Intervals.icu**, computes text embeddings locally, and lets you query your training data conversationally via a Telegram bot powered by OpenAI.

---

## 🏗️ System Architecture

```
Intervals.icu API
      │
      ▼
sync_pipeline.py  ──► Supabase (training_metrics + injury_logs + pgvector embeddings)
                              │
                              ▼
             agent_memory.py (DB access layer)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             chat_agent.py        injury_agent.py
          (conversational Q&A)   (LangGraph risk eval)
                    │
               coach_tools.py
            (LangChain tool set)
                    │
                    ▼
            telegram_bot.py
          (Telegram interface)
```

---

## 📁 File Reference

| File | Purpose |
|---|---|
| `sync_pipeline.py` | ETL script — fetches activities from Intervals.icu, upserts metrics to Supabase, computes and stores pgvector embeddings for workout notes |
| `agent_memory.py` | Supabase DB access layer — all SQL queries used by the agent (activities, metrics, summaries, vector search) |
| `coach_tools.py` | LangChain tool definitions exposed to the LLM — wraps `agent_memory.py` methods as callable tools |
| `chat_agent.py` | Conversational agent — orchestrates the LLM + tool call loop; also runnable as a CLI (`python chat_agent.py`) |
| `injury_agent.py` | LangGraph 3-node pipeline for structured injury risk evaluation (DataRetriever → RiskEvaluator → PrescriptionGenerator) |
| `telegram_bot.py` | Telegram bot interface — wraps `chat_agent.py` with per-user conversation history and commands `/injury`, `/sync`, `/reset` |


---

## 📋 Prerequisites

* **OS:** Linux (Ubuntu/Debian/Fedora) with IPv4 network capabilities (or a configured connection pooler to bypass IPv6 translations).
* **Database:** A **Supabase** instance with the `pgvector` extension enabled.
* **Python:** Version 3.10 or higher (virtual environment recommended).

---

## ⚙️ Installation

```bash
git clone <repo-url>
cd training-coach-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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

# Optional: baseline medical history used by injury_agent.py CLI
# (previous injuries, surgeries, relevant conditions)
ATHLETE_MEDICAL_HISTORY="Neuroma pie derecho en 2024, recaídas con aumento brusco de carga"

# --- TELEGRAM BOT CONFIG ---
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
# Optional: comma-separated Telegram user IDs allowed to use the bot
# Leave empty to allow anyone (not recommended in production)
TELEGRAM_ALLOWED_USER_IDS=123456789
```

---

## 🚀 Running the Services

### Initial database backfill (run once)

```bash
source .venv/bin/activate
python sync_pipeline.py --days 3650
```

### Manual on-demand sync (incremental)

```bash
python sync_pipeline.py --days 7
```

### CLI conversational agent (no Telegram)

```bash
python chat_agent.py
```

### CLI injury-risk assessment

```bash
python injury_agent.py
```

Or pass the symptom query directly:

```bash
python injury_agent.py "Dolor 6/10 en antepié, inflamación, peor al correr más de 30 min"
```

Include one-off medical history in the same command:

```bash
python injury_agent.py --history "Neuroma en pie derecho en 2024" "Dolor 6/10 en antepié hoy"
```

---

## 🔧 Systemd Services (Linux)

A user-level systemd service manages the long-running bot process. The service file lives in `~/.config/systemd/user/`.

### Installed services

| Service | File | Description |
|---|---|---|
| `training-coach-telegram.service` | `~/.config/systemd/user/training-coach-telegram.service` | Telegram bot (`telegram_bot.py`) — runs permanently, auto-restarts on crash |

### Common commands

```bash
# Check status
systemctl --user status training-coach-telegram.service

# Restart after code changes
systemctl --user restart training-coach-telegram.service

# Stop / Start
systemctl --user stop training-coach-telegram.service
systemctl --user start training-coach-telegram.service

# Enable auto-start on login
systemctl --user enable training-coach-telegram.service

# Follow live logs
journalctl --user -u training-coach-telegram.service -f

# Last 50 log lines
journalctl --user -u training-coach-telegram.service -n 50 --no-pager
```

### Telegram bot commands

Once the bot is running, the following commands are available in the Telegram chat:

| Command | Description |
|---|---|
| `/start` | Initialises the bot and shows help |
      | `/medhist set <texto>` | Saves medical/injury background used by `/injury` |
| `/medhist show` | Shows current saved medical history |
| `/medhist clear` | Clears saved medical history |
| `/injury <texto>` | Runs injury-risk assessment from symptoms + context |
| `/sync` | Syncs the last 1 day from Intervals.icu into the database |
| `/sync <N>` | Syncs the last N days (e.g. `/sync 7`) |
| `/reset` | Clears the current conversation history |

### Conversation history limits

- Telegram bot (`telegram_bot.py`): keeps per-user in-memory conversation history capped at the last 10 turns (10 user messages + 10 bot replies). Use `/reset` to clear it.
- CLI chat (`chat_agent.py`): keeps in-memory conversation history for the whole process without an explicit turn cap.
- In both modes, history is not persisted across process restarts.

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

# --- TELEGRAM BOT CONFIG ---
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
# Optional: comma-separated Telegram user IDs allowed to use the bot
# Leave empty to allow anyone (not recommended in production)
TELEGRAM_ALLOWED_USER_IDS=123456789
```

---

## 🚀 Running the Services

### Initial database backfill (run once)

```bash
source .venv/bin/activate
python sync_pipeline.py --days 3650
```

### Manual on-demand sync (incremental)

```bash
python sync_pipeline.py --days 7
```

---

## 🔧 Systemd Services (Linux)

A user-level systemd service manages the long-running bot process. The service file lives in `~/.config/systemd/user/`.

### Installed services

| Service | File | Description |
|---|---|---|
| `training-coach-telegram.service` | `~/.config/systemd/user/training-coach-telegram.service` | Telegram bot (`telegram_bot.py`) — runs permanently, auto-restarts on crash |

### Common commands

```bash
# Check status
systemctl --user status training-coach-telegram.service

# Restart after code changes
systemctl --user restart training-coach-telegram.service

# Stop / Start
systemctl --user stop training-coach-telegram.service
systemctl --user start training-coach-telegram.service

# Enable auto-start on login
systemctl --user enable training-coach-telegram.service

# Follow live logs
journalctl --user -u training-coach-telegram.service -f

# Last 50 log lines
journalctl --user -u training-coach-telegram.service -n 50 --no-pager
```

### Telegram bot commands

Once the bot is running, the following commands are available in the Telegram chat:

| Command | Description |
|---|---|
| `/start` | Initialises the bot and shows help |
| `/medhist set <texto>` | Saves medical/injury background used by `/injury` |
| `/medhist show` | Shows current saved medical history |
| `/medhist clear` | Clears saved medical history |
| `/injury <texto>` | Runs injury-risk assessment from symptoms + context |
| `/sync` | Syncs the last 1 day from Intervals.icu into the database |
| `/sync <N>` | Syncs the last N days (e.g. `/sync 7`) |
| `/reset` | Clears the current conversation history |