from __future__ import annotations
import os
from pathlib import Path

DATA_DIR = os.getenv("ZAR_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "zar-intelligence-2026")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")          # model location
MODEL_FAST = os.getenv("ZAR_MODEL_FAST", "gemini-3.5-flash-lite")
MODEL_REASONING = os.getenv("ZAR_MODEL_REASONING", "gemini-3.5-flash")
LLM_MODE = os.getenv("ZAR_LLM_MODE", "mock")                    # gemini | mock
STORE_MODE = os.getenv("ZAR_STORE_MODE", "memory")              # memory | firestore
SUPABASE_URL = os.getenv("ZAR_SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.getenv("ZAR_SUPABASE_SECRET_KEY", "")   # server only
ASSET_MODE = os.getenv("ZAR_ASSET_MODE", "memory")              # memory | gcs
ASSET_BUCKET = os.getenv("ZAR_ASSET_BUCKET", "")
INVOKE_MODE = os.getenv("ZAR_INVOKE_MODE", "local")             # local | a2a | remote (legacy)
WORKSHOP_URL = os.getenv("WORKSHOP_URL", "http://localhost:8081")
POLARIS_URL = os.getenv("POLARIS_URL", "http://localhost:8080")
TASKS_LOCATION = os.getenv("TASKS_LOCATION", "asia-south1")
TASKS_QUEUE = os.getenv("TASKS_QUEUE", "zar-reminders")
POLARIS_SERVICE_ACCOUNT = os.getenv("POLARIS_SERVICE_ACCOUNT", "")
RELEASE_ID = os.getenv("ZAR_RELEASE_ID", "cup-2026.10-r9")
DOMAINS = [d.strip() for d in os.getenv("ZAR_DOMAINS", "retail").split(",") if d.strip()]
MAX_AUTO_STEPS = int(os.getenv("ZAR_MAX_AUTO_STEPS", "8"))
TRACE_DEBUG = os.getenv("ZAR_TRACE_DEBUG", "1") == "1"       # keep prompts/raw outputs in traces
ORDER_WEBHOOK_URL = os.getenv("ZAR_ORDER_WEBHOOK_URL", "")    # empty = sandbox handoff
ORDER_WEBHOOK_SECRET = os.getenv("ZAR_ORDER_WEBHOOK_SECRET", "dev-secret")
