# nocodb_writer.py
#
# Поле Text собирается в порядке:
#   1. Текст подписи (raw_text без голого URL)
#   2. Описание из платформы (yt-dlp description / og:description)
#   3. Краткое резюме транскрипта (summary)
#   4. Полный форматированный транскрипт
#   5. Vision description (для фото)
#
# NocoDB Long Text лимит: ~1 МБ на запись (~500 000 символов) — практически неограничен

import os
import mimetypes
import requests
import logging
from datetime import datetime
from config import cfg
from detector import ContentType, PLATFORM_LABELS
from text_utils import translate_to_russian

logger = logging.getLogger(__name__)

NOCODB_API = cfg.NOCODB_BASE_URL + '/api/v2'

URL_CONTENT_TYPES = {
    ContentType.YOUTUBE,
    ContentType.INSTAGRAM,
    ContentType.TIKTOK,
    ContentType.VK,
    ContentType.VK_WALL,
    ContentType.LINKEDIN,
    ContentType.TELEGRAM,
    ContentType.OTHER_URL,   # Habr, Medium, любые внешние ссылки
}


def get_headers():
    return {
        'xc-token':     cfg.NOCODB_TOKEN,
        'Content-Type': 'application/json',
    }


# ── Загрузка файлов ───────────────────────────────────────────────────────────

def upload_file(local_path: str) -> list:
    try:
        size = os.path.getsize(local_path)
        if size > 5 * 1024 * 1024:
            logger.warning(os.path.basename(local_path) + ' > 5 МБ, пропускаем')
            return []

        mime, _  = mimetypes.guess_type(local_path)
        mime     = mime or 'application/octet-stream'
        filename = os.path.basename(local_path)

        with open(local_path, 'rb') as f:
            resp = requests.post(
                NOCODB_API + '/storage/upload',
                headers={'xc-token': cfg.NOCODB_TOKEN},
                files={'file': (filename, f, mime)},
                timeout=60
            )

        if not resp.ok:
            logger.error('Upload ' + str(resp.status_code) + ': ' + resp.text[:200])
            return []

        data   = resp.json()
        result = data if isinstance(data, list) else [data]
        logger.info('Загружен ' + filename)
        return result

    except Exception as e:
        logger.error('upload_file: ' + str(e))
        return []


def upload_files(paths: list) -> list:
    result = []
    for p in paths:
        if p and os.path.exists(p):
            result.extend(upload_file(p))
    return result


# ── Утилиты форматирования ────────────────────────────────────────────────────

def _has_russian_text(text: str) -> bool:
    """Проверяет, содержит ли текст хотя бы одну русскую букву."""
    if not text:
        return True
    
    import re
    # Ищем любую кириллическую букву
    if re.search(r'[а-яА-ЯёЁ]', text):
        return True
        
    # Если кириллицы нет, но при этом нет и других букв (например, одни цифры, ссылки или смайлы)
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
        
    return False


def _escape_markdown_headings(text: str) -> str:
    """Экранирует # в начале строк, чтобы NocoDB не рендерил Markdown headings."""
    if not text:
        return text
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('#'):
            indent = line[:len(line) - len(stripped)]
            line = indent + '\\' + stripped
        result.append(line)
    return '\n'.join(result)


# ── Построение поля Text ──────────────────────────────────────────────────────

def build_text_field(content: dict, meta: dict) -> str:
    """
    Структура Text для видео с транскриптом:
    ──────────────────────────────────────
    [подпись автора поста]

    [описание с платформы]

    📋 Краткое содержание:
    [3-5 предложений summary]

    📝 Транскрипция:
    [полный форматированный текст речи]
    ──────────────────────────────────────

    Структура Text для фото/поста без видео:
    ──────────────────────────────────────
    [подпись]

    🖼 Описание изображения:
    [vision description]
    ──────────────────────────────────────
    """
    # 1. Очистка от шума соцсетей (лайки, комменты, даты, авторы)
    import re
    
    def clean_social_text(text: str) -> str:
        if not text: return ""
        # 1. Паттерн для лайков и комментов
        text = re.sub(r'\d+[\d,.]*[KkMm]?\s+(likes|comments|likes|views|followers|following).*?', '', text, flags=re.IGNORECASE)
        # 2. Паттерн для строки автора и даты: "user January 16, 2026:"
        _MONTHS = 'January|February|March|April|May|June|July|August|September|October|November|December'
        text = re.sub(r'[,\s-]*\b\w+\b\s+(?:' + _MONTHS + r')\s+\d{1,2},\s+\d{4}:?', '', text)
        # 3. Чистим артефакты в начале строки (запятые, тире)
        text = re.sub(r'^[,\s-]+', '', text)
        
        return "\n".join([line.strip() for line in text.split('\n') if line.strip()])

    ctype       = content['type']
    raw         = clean_social_text(content.get('raw_text') or '')
    url         = (content.get('url') or '').strip()
    transcript  = (content.get('transcript') or '').strip()
    summary     = (content.get('summary') or '').strip()
    vision      = (content.get('vision_description') or '').strip()
    is_video    = content.get('is_video', True)   # False = фото/пост из соцсети

    # Описание из платформы (yt-dlp description, og:description, сгенерированное)
    platform_desc = clean_social_text(meta.get('description') or '')

    # Текст подписи без голого URL
    caption = raw.replace(url, '').strip() if url else raw
    if len(caption) < 4:
        caption = ''

    logger.info(
        'build_text_field:'
        ' caption=' + str(len(caption)) +
        ' platform_desc=' + str(len(platform_desc)) +
        ' vision=' + str(len(vision)) +
        ' summary=' + str(len(summary)) +
        ' transcript=' + str(len(transcript))
    )

    parts = []

    # 1. Подпись пользователя / текст поста
    if caption:
        parts.append(caption)

    # 2. Описание с платформы — для видео и постов (не для фото с подписью)
    if platform_desc and platform_desc not in caption:
        parts.append(platform_desc)

    # 3. Краткое резюме транскрипта
    if summary:
        parts.append('📋 Краткое содержание:\n\n' + summary) # Двойной перенос для изоляции стиля

    # 4. Полная транскрипция
    if transcript:
        parts.append('📝 Транскрипция:\n\n' + transcript)

    # 5. Описание изображений (если есть)
    vision_description = (content.get('vision_description') or '').strip()
    if vision_description:
        # Решение 10: Увеличиваем лимит до 1000, чтобы не обрезать карусели.
        # Форматирование (шрифт) исправлено за счёт использования пуль в media_handlers.
        v_desc = vision_description[:1000] + ('...' if len(vision_description) > 1000 else '')
        parts.append('🖼 Описание изображения:\n\n' + v_desc)

    # 6. Перевод (только если текст действительно осмысленный и НЕ на русском)
    text_for_translate = "\n\n".join([p for p in [caption, platform_desc, transcript] if p])
    
    should_translate = False
    if text_for_translate:
        # Если текст уже содержит русский — пропускаем
        if _has_russian_text(text_for_translate):
            should_translate = False
        # Переводим только если текст длиннее 50 символов (исключаем ники/короткие фразы)
        elif len(text_for_translate) > 50:
            should_translate = True
        # Если текст короткий, проверяем нет ли в нем @ (ник)
        elif '@' not in text_for_translate and len(text_for_translate.split()) > 3:
            # Если это короткая фраза из 4+ слов без @ — переводим
            should_translate = True
    
    if should_translate:
        translation = translate_to_russian(text_for_translate)
    else:
        translation = ""

    final_parts = []
    if translation and translation.strip():
        final_parts.append("🌐 ПЕРЕВОД:\n" + translation)
        final_parts.append("────────────────────")

    final_parts.extend(parts)
    return _escape_markdown_headings('\n\n'.join(final_parts))


# ── Построение записи ─────────────────────────────────────────────────────────

def build_record(task: dict, category: str, meta: dict,
                 theme_title: str, author: str, attachments: list) -> dict:
    content  = task['content']
    ctype    = content['type']
    now      = datetime.now().strftime('%Y-%m-%d %H:%M')
    platform = PLATFORM_LABELS.get(ctype, 'Other')

    text_value = build_text_field(content, meta)
    url_value  = (content.get('url') or '') if ctype in URL_CONTENT_TYPES else ''

    record = {
        'Theme':    theme_title,
        'Date':     now,
        'Text':     text_value,
        'URL':      url_value,
        'Platform': platform,
        'Category': category,
        'Author':   author,
    }

    if attachments:
        record['Attachment'] = attachments

    return record


# ── Создание записи ───────────────────────────────────────────────────────────

def post_record(record: dict) -> str:
    table_id = cfg.NOCODB_TABLE_ID
    if not table_id:
        logger.error('NOCODB_TABLE_ID не задан в .env')
        return ''

    # Диагностика — видим что именно отправляем
    text_len = len(record.get('Text', ''))
    logger.info(
        'POST record → Text: ' + str(text_len) + ' символов'
        + (' [есть транскрипция]' if '📝 Транскрипция' in record.get('Text', '') else '')
        + (' [есть summary]'      if '📋 Краткое'      in record.get('Text', '') else '')
    )

    resp = requests.post(
        NOCODB_API + '/tables/' + table_id + '/records',
        json=record,
        headers=get_headers(),
        timeout=60   # увеличен: большой JSON с транскриптом может идти долго
    )

    if not resp.ok:
        logger.error('NocoDB ' + str(resp.status_code) + ': ' + resp.text[:300])
        resp.raise_for_status()

    row_id = str(resp.json().get('Id', ''))
    logger.info('Id=' + row_id + ' Category=' + record.get('Category', ''))
    return row_id


def write_record(task: dict, category: str, meta: dict,
                 theme_title: str, author: str,
                 local_file_paths: list) -> str:
    try:
        attachments = upload_files(local_file_paths)
        record      = build_record(task, category, meta, theme_title, author, attachments)
        return post_record(record)
    except requests.exceptions.HTTPError:
        return ''
    except Exception as e:
        logger.error('write_record: ' + str(e))
        return ''








