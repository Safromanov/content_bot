# config.py

import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ──────────────────────────────────────────
    TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN', '')
    ALLOWED_USER_ID  = int(os.getenv('ALLOWED_USER_ID', '0'))

    # ── Groq ──────────────────────────────────────────────
    GROQ_API_KEY     = os.getenv('GROQ_API_KEY', '')
    GROQ_MODEL_SMART = 'llama-3.3-70b-versatile'   # классификация, заголовок
    GROQ_MODEL_FAST  = 'llama-3.1-8b-instant'      # описание, форматирование

    # ── NocoDB ────────────────────────────────────────────
    NOCODB_TOKEN    = os.getenv('NOCODB_TOKEN', '')
    NOCODB_BASE_URL = 'https://app.nocodb.com'
    NOCODB_TABLE_ID = os.getenv('NOCODB_TABLE_ID', '')

    # ── Категории ─────────────────────────────────────────
    CATEGORIES = [
        'Photo', 'Sport', 'renovation', 'AI',
        'information security', 'Health', 'Cinema', 'Cooking',
        'Travel', 'Games', 'Clothes', 'marketplace',
        'Design', 'Coding', 'Vacancy', 'other',
    ]
    DEFAULT_CATEGORY = 'other'

    # Порог уверенности классификатора — ниже этого значения
    # бот спрашивает пользователя о подтверждении категории
    CLASSIFY_CONFIDENCE_THRESHOLD = 0.65

    # ── Retry ─────────────────────────────────────────────
    RETRY_ATTEMPTS = 3          # попыток на задачу
    RETRY_BASE_DELAY = 1.0      # секунд (экспоненциальный backoff: 1, 3, 9)
    DLQ_FILE = 'dead_letter.json'

    # ── Прочее ────────────────────────────────────────────
    # ── Instagram / yt-dlp ────────────────────────────────
    # Как получить cookies:
    #   На сервере: yt-dlp --cookies-from-browser chrome --skip-download <url>
    #   Или вручную через расширение 'Get cookies.txt LOCALLY' и указать путь в .env
    INSTAGRAM_COOKIES_FILE = os.getenv('INSTAGRAM_COOKIES_FILE', '')
    CAROUSEL_MAX_IMAGES    = int(os.getenv('CAROUSEL_MAX_IMAGES', '10'))

    # ── VK ────────────────────────────────────────────────
    VK_SERVICE_TOKEN = os.getenv('VK_SERVICE_TOKEN', '')
    VK_API_VERSION   = '5.199'

    # ── Прочее ────────────────────────────────────────────
    TEMP_DIR  = '/tmp/tg-bot'
    STATS_FILE = 'stats.json'


cfg = Config()
os.makedirs(cfg.TEMP_DIR, exist_ok=True)


def validate_config() -> None:
    """
    Проверяет обязательные переменные при старте.
    Завершает процесс с понятным сообщением если что-то не задано.
    """
    required = {
        'TELEGRAM_TOKEN': cfg.TELEGRAM_TOKEN,
        'GROQ_API_KEY':   cfg.GROQ_API_KEY,
        'NOCODB_TOKEN':   cfg.NOCODB_TOKEN,
        'NOCODB_TABLE_ID': cfg.NOCODB_TABLE_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print('❌ Не заданы обязательные переменные в .env:')
        for m in missing:
            print('   ' + m)
        sys.exit(1)
# Patch: Instagram cookies + carousel settings added below cfg instantiation




