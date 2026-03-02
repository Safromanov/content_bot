# text_utils.py

import base64
import logging
from config import cfg
from groq_client import get_groq

logger = logging.getLogger(__name__)

TITLE_TEXT_LIMIT = 800
CLASSIFY_TEXT_LIMIT = 2000


def analyze_photo(file_path: str) -> str:
    """Vision: описание содержимого фото на русском."""
    try:
        with open(file_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        response = get_groq().chat.completions.create(
            model='meta-llama/llama-4-scout-17b-16e-instruct',
            max_tokens=200,
            messages=[{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
                {'type': 'text', 'text': 'Describe what is shown in the photo in Russian. One very short sentence (max 15 words). Only core content.'}
            ]}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error('analyze_photo: ' + str(e))
        return ''


def generate_theme_title(text: str) -> str:
    """70b — заголовок до 10 слов на языке текста."""
    if not text or not text.strip():
        return 'Без названия'
    try:
        response = get_groq().chat.completions.create(
            model=cfg.GROQ_MODEL_SMART,
            max_tokens=30,
            temperature=0.0,
            messages=[{'role': 'user', 'content': (
                'Напиши заголовок для текста ниже на русском языке.\n'
                'Правила: максимум 10 слов, отражает главную тему, '
                'без кавычек, без точки в конце. Выводи ТОЛЬКО заголовок.\n'
                'Если текст выглядит как бессмысленный набор символов (шум), напиши "Без названия".\n\n'
                'Текст:\n' + text.strip()[:TITLE_TEXT_LIMIT]
            )}]
        )
        raw   = response.choices[0].message.content.strip().strip('"\'')
        words = raw.split()
        return ' '.join(words[:10])[:150] if raw else 'Без названия'
    except Exception as e:
        logger.warning('generate_theme_title: ' + str(e))
        return ' '.join(text.split()[:8])


def generate_summary(text: str) -> str:
    """3-5 предложений на языке оригинала."""
    if not text or len(text.strip()) < 100:
        return ''
    try:
        response = get_groq().chat.completions.create(
            model=cfg.GROQ_MODEL_FAST,
            max_tokens=300,
            temperature=0.2,
            messages=[{'role': 'user', 'content': (
                'Напиши краткое содержание текста ниже в 3-5 предложениях НА РУССКОМ ЯЗЫКЕ. '
                'Выпиши только ключевые мысли.\n'
                'Если текст предствляет собой бессмысленный набор символов, напиши "Нет смыслового содержания."\n\n'
                'Текст:\n' + text.strip()[:3000]
            )}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning('generate_summary: ' + str(e))
        return ''


def generate_video_description(title: str) -> str:
    """Резервное описание по заголовку когда нет транскрипта."""
    if not title:
        return ''
    try:
        response = get_groq().chat.completions.create(
            model=cfg.GROQ_MODEL_FAST,
            max_tokens=200,
            temperature=0.3,
            messages=[{'role': 'user', 'content': (
                'По названию видео напиши краткое описание его содержания на русском. '
                '3-4 предложения. Только суть, без упоминания автора или канала.\n\n'
                'Название: ' + title
            )}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning('generate_video_description: ' + str(e))
        return ''


def translate_to_russian(text: str) -> str:
    """Переводит текст на русский язык, если он не на русском."""
    if not text or not text.strip() or len(text.strip()) < 10:
        return ""
    try:
        response = get_groq().chat.completions.create(
            model=cfg.GROQ_MODEL_FAST,
            max_tokens=2000,
            temperature=0.1,
            messages=[{'role': 'user', 'content': (
                "Translate the following text to Russian. "
                "RULES: "
                "1. Output ONLY the translated text. "
                "2. DO NOT write 'Перевод:', 'Translation:', or any other prefixes. "
                "3. If the text is already in Russian, return ONLY the word 'ORIGINAL'. "
                "4. If it's noise or random symbols, return 'ORIGINAL'.\n\n"
                "Text:\n" + text.strip()[:CLASSIFY_TEXT_LIMIT]
            )}]
        )
        translated = response.choices[0].message.content.strip()
        # Убираем возможные галлюцинации префиксов
        for prefix in ["Перевод:", "Translation:", "Текст на русском:"]:
            if translated.lower().startswith(prefix.lower()):
                translated = translated[len(prefix):].strip()
        
        if translated.upper().startswith("ORIGINAL"):
            return ""
        logger.info(f"Перевод выполнен: {len(translated)} симв.")
        return translated.strip('"\' ')
    except Exception as e:
        logger.warning('translate_to_russian: ' + str(e))
        return ""
