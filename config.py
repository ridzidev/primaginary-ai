import os
import sys
from pathlib import Path
from dotenv import load_dotenv

GLOBAL_DIR = Path.home() / ".primaginary-ai"
load_dotenv(GLOBAL_DIR / ".env")

# Workspace otomatis mendeteksi folder tempat terminalmu sedang berada
WORKSPACE = Path.cwd().resolve()

if (WORKSPACE / ".env").is_file():
    load_dotenv(WORKSPACE / ".env", override=False)

API_BASE_URL = os.getenv("API_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile").strip()

MIN_REQUEST_INTERVAL = float(os.getenv("MIN_REQUEST_INTERVAL", "2.0"))
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "35"))
COMMAND_TIMEOUT = float(os.getenv("COMMAND_TIMEOUT", "90.0"))
MAX_COMMAND_OUTPUT_CHARS = 5000

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", ".idea", ".vscode"
}
