import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
BUILDS_DIR = DATA_DIR / "builds"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "history.db"
CHECKLIST_PATH = ROOT / "checklist.json"
SAMPLES_DIR = ROOT / "samples"

def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    if "streamlit" in sys.modules:
        try:
            import streamlit as st
            if name in st.secrets:
                return str(st.secrets[name])
        except Exception:
            pass
    return default


GROQ_API_KEY = _setting("GROQ_API_KEY")
GROQ_MODEL = _setting("GROQ_MODEL", "openai/gpt-oss-120b")

REA_ENABLED = _setting("REA_ENABLED", "false").lower() in ("1", "true", "yes")
REA_COMMAND = _setting("REA_COMMAND", "npx -y @morluto/rea mcp")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_FILES = int(os.getenv("MAX_FILES", "5000"))
MAX_TEXT_FILE_MB = 2
MAX_HITS_PER_CHECK = 20
EXPLORE_STEPS = int(os.getenv("EXPLORE_STEPS", "6"))

for d in (DATA_DIR, BUILDS_DIR, UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def llm_available() -> bool:
    return bool(GROQ_API_KEY) and GROQ_API_KEY != "your_groq_key_here"
