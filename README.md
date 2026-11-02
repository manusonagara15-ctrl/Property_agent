# Property Agent Bot

A local Streamlit property search, listing management, AI assistant, CRM, reviews, duplicate-checking, backup and business-insights app using SQLite and optional Ollama.

## Runtime structure

- `app.py` — the complete active application, including UI/CSS, SQLite helpers, CRM logic, recommendations, local AI filter extraction and property search.
- `init_db.py` — optional first-time database initializer. Do not run it over your working data unless you have a backup; it seeds sample rows only when the properties table is empty.
- `data/property_agent.db` — the existing project database. Keep it in place.
- `assets/properties/` — bundled property photos and uploaded images. Keep this folder in place.
- `backup/` — historical source snapshots; not imported by the running app.
- `AUDIT_REPORT.md` — findings from the source and database checks.

The active `app.py` no longer imports `frontend/`, `backend/`, or `services/`; the runtime logic is in one file. The folders from the original archive were reviewed, and the duplicate module copies are omitted from this clean package so there is a single active source of truth.

## Windows setup

```powershell
cd "C:\Users\manus\OneDrive\Desktop\property-agent-bot"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m py_compile app.py init_db.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`requirements.txt` contains the application dependencies. The supplied database is included in the ZIP; do not run `init_db.py` unless you intentionally want to initialize a different/new database.

## Local AI (optional)

Install Ollama and pull the model:

```powershell
ollama pull llama3.2:3b
```

Start Ollama, then run the app. Defaults can be changed with `OLLAMA_GENERATE_URL` and `OLLAMA_MODEL`. Property search still has a local parser fallback when Ollama is unavailable.

## Safety

Make a copy of `data/property_agent.db` before database restoration, duplicate cleanup, or destructive changes. The app creates a backup before restoring a selected database.
