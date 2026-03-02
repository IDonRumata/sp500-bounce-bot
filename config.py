import os
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# --- Finnhub ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# --- Schedule ---
SCHEDULE_DAYS = os.getenv("SCHEDULE_DAYS", "mon,wed,fri")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "8"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))

# --- Analysis thresholds ---
MAX_PRICE = float(os.getenv("MAX_PRICE", "200"))
MIN_DRAWDOWN = float(os.getenv("MIN_DRAWDOWN", "-10"))
MIN_COMPOSITE_SCORE = float(os.getenv("MIN_COMPOSITE_SCORE", "58"))
TOP_PICKS_COUNT = int(os.getenv("TOP_PICKS_COUNT", "7"))
PRE_FILTER_RSI = float(os.getenv("PRE_FILTER_RSI", "45"))

# --- Scoring weights ---
WEIGHT_TECHNICAL = 0.40
WEIGHT_FUNDAMENTAL = 0.30
WEIGHT_SENTIMENT = 0.15
WEIGHT_MARKET = 0.15

# --- Sector ETFs for rotation analysis ---
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("sp500bot")
