# Copilot Instructions for training-coach-agent

## Project purpose
- This repository is an endurance training coach and injury-prevention assistant.
- It syncs activity data from Intervals.icu into Supabase/Postgres with pgvector embeddings.
- Main user interface is a Telegram bot; local CLI chat is also supported.

## Core architecture
- `sync_pipeline.py`: ETL sync from Intervals.icu to Supabase, including embedding generation.
- `agent_memory.py`: DB/data access layer for queries and vector retrieval.
- `coach_tools.py`: Tool wrappers exposed to the LLM.
- `chat_agent.py`: Main conversational tool-calling agent.
- `injury_agent.py`: Structured injury-risk workflow (LangGraph nodes).
- `telegram_bot.py`: Telegram interface and command handlers.

## Environment and dependencies
- Python 3.10+ in `.venv`.
- Dependencies from `requirements.txt`.
- Configuration is via `.env` at project root.
- Required services/APIs: Intervals.icu, Supabase/Postgres (pgvector), OpenAI, Telegram Bot API.

## Code style and change rules
- Keep changes minimal and scoped to the request.
- Preserve existing module boundaries and avoid large refactors unless requested.
- Prefer explicit, readable Python over clever abstractions.
- Add concise docstrings/comments only when behavior is non-obvious.
- Do not introduce breaking API changes across modules unless requested.

## Agent and tool behavior expectations
- When changing tool-call behavior, ensure `coach_tools.py` and `chat_agent.py` remain aligned.
- If query/data semantics change, update `agent_memory.py` first, then dependent tools.
- Keep injury-related logic deterministic and auditable in `injury_agent.py`.
- For Telegram changes, preserve `/start`, `/sync`, and `/reset` command behavior unless explicitly asked.

## Data and safety constraints
- Never hardcode secrets or tokens.
- Never commit `.env` values or real credentials.
- Avoid destructive SQL changes in normal feature edits.
- Keep user-facing recommendations conservative for health/injury topics.
- Prefer explaining uncertainty instead of overconfident medical claims.

## Local run and verification
- Set up environment:
  - `python -m venv .venv`
  - `source .venv/bin/activate`
  - `pip install -r requirements.txt`
- Sync data:
  - `python sync_pipeline.py --days 7`
- Run CLI chat:
  - `python chat_agent.py`
- Run Telegram bot:
  - `python telegram_bot.py`

## Systemd context (Linux user service)
- Service name: `training-coach-telegram.service`.
- Typical operations:
  - `systemctl --user status training-coach-telegram.service`
  - `systemctl --user restart training-coach-telegram.service`
  - `journalctl --user -u training-coach-telegram.service -n 50 --no-pager`

## When generating code in this repo
- Prefer edits that keep backwards compatibility for existing bot commands and data schema.
- Include lightweight validation/error handling around API and DB calls.
- If introducing new config keys, document them in `README.md` `.env` section.
- If behavior changes materially, update README usage notes.