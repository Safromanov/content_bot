# groq_client.py
# Синглтон Groq клиента — создаётся один раз при импорте.
# Избегает overhead создания нового HTTP-соединения при каждом вызове.

from groq import Groq
from content_bot.config import cfg

_client: Groq | None = None


def get_groq() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=cfg.GROQ_API_KEY)
    return _client
