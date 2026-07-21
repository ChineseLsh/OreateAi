import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


BASE_URL = "https://www.oreateai.com"
CDN_URL = "https://cdn.oreateai.com"

PASSPORT_API = f"{BASE_URL}/passport/api"
OREATE_API = f"{BASE_URL}/oreate"
BIZ_API = f"{BASE_URL}/bizapi"

DEFAULT_PROXY = os.getenv("THREADAI_PROXY", "http://127.0.0.1:7897").strip() or None

BROWSER_CHANNEL = os.getenv("THREADAI_BROWSER_CHANNEL", "chrome").strip() or "chrome"
BROWSER_HEADLESS = _env_bool("THREADAI_BROWSER_HEADLESS", True)
BROWSER_TIMEOUT_MS = _env_int("THREADAI_BROWSER_TIMEOUT_MS", 60_000)
BROWSER_RISK_TIMEOUT_MS = _env_int("THREADAI_BROWSER_RISK_TIMEOUT_MS", 15_000)

EMAIL_PROVIDER = os.getenv("THREADAI_EMAIL_PROVIDER", "auto").strip().lower() or "auto"
ALLOW_PLUS_EMAIL = _env_bool("THREADAI_ALLOW_PLUS_EMAIL", False)

LUCKMAIL_API_KEY = os.getenv("LUCKMAIL_API_KEY", "").strip()
LUCKMAIL_API_SECRET = os.getenv("LUCKMAIL_API_SECRET", "").strip()
LUCKMAIL_BASE_URL = os.getenv("LUCKMAIL_BASE_URL", "https://mails.luckyous.com").rstrip("/")
LUCKMAIL_PROXY = os.getenv("LUCKMAIL_PROXY", "").strip()
LUCKMAIL_HTTP_RETRIES = _env_int("LUCKMAIL_HTTP_RETRIES", 3)
LUCKMAIL_MODE = os.getenv("LUCKMAIL_MODE", "project_order").strip().lower() or "project_order"
LUCKMAIL_PROJECT_CODE = os.getenv("LUCKMAIL_PROJECT_CODE", "grok").strip() or "grok"
LUCKMAIL_EMAIL_TYPE = os.getenv("LUCKMAIL_EMAIL_TYPE", "ms_imap").strip().lower() or "ms_imap"
LUCKMAIL_DOMAIN = os.getenv("LUCKMAIL_DOMAIN", "outlook.com").strip().lower() or "outlook.com"
LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS = _env_int("LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS", 10)
LUCKMAIL_ORDER_TIMEOUT = _env_int("LUCKMAIL_ORDER_TIMEOUT", 300)
LUCKMAIL_ORDER_POLL_INTERVAL = _env_int("LUCKMAIL_ORDER_POLL_INTERVAL", 3)
LUCKMAIL_INVENTORY_CACHE_SECONDS = _env_int("LUCKMAIL_INVENTORY_CACHE_SECONDS", 60)
LUCKMAIL_POLL_INTERVAL = _env_int("LUCKMAIL_POLL_INTERVAL", 5)
LUCKMAIL_RECENT_SECONDS = _env_int("LUCKMAIL_RECENT_SECONDS", 900)
LUCKMAIL_IMAP_HOSTS = tuple(
    host.strip()
    for host in os.getenv(
        "LUCKMAIL_IMAP_HOSTS", "outlook.office365.com,imap-mail.outlook.com"
    ).split(",")
    if host.strip()
)
LUCKMAIL_IMAP_LAST_N = _env_int("LUCKMAIL_IMAP_LAST_N", 30)
LUCKMAIL_REQUIRE_RECIPIENT_MATCH = _env_bool("LUCKMAIL_REQUIRE_RECIPIENT_MATCH", True)
LUCKMAIL_IMAP_PROXY = os.getenv("LUCKMAIL_IMAP_PROXY", "").strip()

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/home/index/zh",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "client-type": "pc",
    "locale": "zh-CN",
}

REGISTER_POINTS = 80
