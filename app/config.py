import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

API_ID = int(os.getenv("API_ID", "0") or "0")
API_HASH = os.getenv("API_HASH", "")
PHONE = os.getenv("PHONE", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_API_MODE = os.getenv("GEMINI_API_MODE", "openai")
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
)

SESSION_NAME = "leadgen"
DB_PATH = os.path.join(BASE_DIR, "leadgen.db")
KB_DIR = os.path.join(BASE_DIR, "kb")

# YetReply
YETREPLY_BASE_URL = os.getenv("YETREPLY_BASE_URL", "https://yetreply.ru")
YETREPLY_USERNAME = os.getenv("YETREPLY_USERNAME", "")
YETREPLY_PASSWORD = os.getenv("YETREPLY_PASSWORD", "")
YETREPLY_TOKEN = os.getenv("YETREPLY_TOKEN", "")

# Имя оператора для ответа на «как вас зовут?».
# Пусто — бот никогда не называет имя и не выдумывает его.
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "").strip()
