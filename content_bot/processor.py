# processor.py
# Чистый роутер: выбирает handler по типу контента, запускает обогащение,
# параллельно классифицирует + генерирует заголовок, пишет в NocoDB.

import asyncio
import logging
import time
import uuid
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from content_bot.detector import ContentType, PLATFORM_LABELS
from content_bot.media.handlers import get_handler
from content_bot.ai.classifier import classify, ClassifyResult
from content_bot.ai.text_utils import generate_theme_title
from content_bot.storage.nocodb import write_record
from content_bot.storage.stats import record as record_stats
from content_bot.config import cfg

logger = logging.getLogger(__name__)

CLASSIFY_TEXT_LIMIT = 2000


# ── Текст для классификации ───────────────────────────────────────────────────

def _meaningful_text(raw: str, url: str) -> str:
    if not raw:
        return ''
    cleaned = raw.replace(url, '').strip() if url else raw.strip()
    return cleaned if len(cleaned) >= 4 else ''


def _build_classify_text(content: dict) -> str:
    """
    Приоритет источников:
    1. transcript
    2. platform_description (нативный caption из yt-dlp)
    3. vision_description
    4. raw_text (без голого URL)
    5. title + description из meta
    """
    raw                  = (content.get('raw_text') or '').strip()
    url                  = (content.get('url') or '').strip()
    transcript           = (content.get('transcript') or '').strip()
    vision               = (content.get('vision_description') or '').strip()
    platform_description = (content.get('platform_description') or '').strip()
    meaningful           = _meaningful_text(raw, url)
    meta                 = content.get('_meta') or {}
    ctype                = content['type']

    parts = []
    
    if meaningful:
        parts.append(meaningful)
        
    if platform_description and platform_description not in meaningful:
        parts.append(platform_description)
        
    if transcript:
        parts.append(transcript[:CLASSIFY_TEXT_LIMIT])
    elif vision and ctype in (ContentType.PHOTO, ContentType.VIDEO,
                              ContentType.INSTAGRAM, ContentType.TIKTOK, ContentType.VK):
        parts.append(vision)
    elif meta.get('description') and meta['description'] not in meaningful:
        parts.append(meta['description'])
        
    if parts:
        return '\n\n'.join(parts)

    if ctype in (ContentType.TEXT, ContentType.PHOTO,
                 ContentType.VIDEO, ContentType.AUDIO):
        if raw:
            return raw[:600] + (' ... ' + raw[-200:] if len(raw) > 800 else '')
        return ctype.value

    title = meta.get('title', '')
    desc  = meta.get('description', '')
    return (title + ' ' + desc).strip() or ''


# ── Заголовок ─────────────────────────────────────────────────────────────────

def _build_theme_title(content: dict, meta: dict, classify_text: str) -> str:
    ctype      = content['type']
    transcript = (content.get('transcript') or '').strip()
    vision     = (content.get('vision_description') or '').strip()
    platform_description = (content.get('platform_description') or '').strip()
    url_str    = (content.get('url') or '').strip()
    raw_text   = (content.get('raw_text') or '').strip()
    has_caption = bool(_meaningful_text(raw_text, url_str))

    if ctype == ContentType.YOUTUBE:
        return meta.get('title', '') or 'YouTube видео'

    # Если транскрипт достаточно длинный — используем только его
    if transcript and len(transcript) >= 80:
        return generate_theme_title(transcript[:600])

    # Иначе объединяем доступные источники: транскрипт + описание платформы + vision
    # Это нужно для коротких рилсов, где транскрипт содержит лишь CTA вроде
    # «Пишите «Нейрофит»» (23 символа) — описание даёт реальный контекст темы
    title_context = []
    if platform_description:
        title_context.append(platform_description)
    if transcript:
        title_context.append(transcript)
    if vision and not title_context:
        title_context.append(vision)

    if title_context:
        return generate_theme_title("\n".join(title_context))

    if meta.get('description'):
        return generate_theme_title(meta['description'])
    
    if ctype in (ContentType.INSTAGRAM, ContentType.TIKTOK, ContentType.VK) and not has_caption:
        return meta.get('title', '') or ''
    return generate_theme_title(classify_text) if classify_text else ''


# ── Основной обработчик ───────────────────────────────────────────────────────

async def process_task(task: dict, app) -> str:
    # Уникальный ID задачи — проходит через все логи
    task_id = task.get('task_id') or uuid.uuid4().hex[:8]
    task['task_id'] = task_id

    content  = task['content']
    ctype    = content['type']
    chat_id  = task['chat_id']
    platform = PLATFORM_LABELS.get(ctype, 'Other')

    prefix = '[' + task_id + '] '
    logger.info(prefix + 'Обрабатываю: ' + ctype.value)

    t_start = time.monotonic()
    success = False

    try:
        # ── 1. Обогащение данными платформы ───────────────
        handler = get_handler(ctype)
        content = await handler.enrich(content, app.bot, task_id)
        task['content'] = content

        meta = content.pop('_meta', {}) or {}

        # ── 2. Параллельно: classify + theme_title ─────────
        classify_text = _build_classify_text(content)
        logger.info(prefix + 'Classify text (' + str(len(classify_text)) + '): ' + classify_text[:100])

        loop = asyncio.get_event_loop()

        if classify_text.strip():
            classify_future = loop.run_in_executor(None, classify, classify_text, task_id)
            title_future    = loop.run_in_executor(
                None, _build_theme_title, content, meta, classify_text
            )
            classify_result, theme_title = await asyncio.gather(
                classify_future, title_future
            )
        else:
            classify_result = ClassifyResult(cfg.DEFAULT_CATEGORY, 0.0)
            theme_title     = meta.get('title', '') or ''

        category   = classify_result.category
        confidence = classify_result.confidence

        logger.info(prefix + 'Категория: ' + category +
                    ' (conf=' + str(round(confidence, 2)) + ')')
        logger.info(prefix + 'Theme: ' + theme_title)

        meta_author = meta.get('author', '').strip()
        forward_author = content.get('forward_author', '').strip()
        final_author = meta_author if meta_author else forward_author

        logger.info(prefix + 'Author: ' + final_author)

        # ── 3. Запись в NocoDB ─────────────────────────────
        row_id = write_record(
            task=task,
            category=category,
            meta=meta,
            theme_title=theme_title,
            author=final_author,
            local_file_paths=content.get('local_files', []),
        )

        # ── 4. Удаление временных файлов (перенесено в finally) ───

        # ── 5. Ответ пользователю ─────────────────────────
        if row_id:
            success = True
            has_transcript = bool(content.get('transcript'))
            source = ' [транскрипт]' if has_transcript else ''
            msg    = '✅ Добавлено!' + source + '\nКатегория: ' + category

            # Уточнение при низкой уверенности
            if confidence < cfg.CLASSIFY_CONFIDENCE_THRESHOLD and confidence > 0:
                top2 = [c for c in cfg.CATEGORIES if c != category][:2]
                msg += ('\n\n🤔 Уверен на ' + str(round(confidence * 100)) + '%.'
                        + ' Возможно это: ' + ' или '.join(top2[:2]) + '?'
                        + '\nКатегорию можно изменить кнопкой ниже.')

            msg += '\nTheme: ' + theme_title
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton('Изменить категорию', callback_data='re:' + row_id)
            ]])
        else:
            msg = '⚠️ Обработано, но записать не удалось. Проверь логи.'
            reply_markup = None

        await app.bot.send_message(chat_id=chat_id, text=msg, reply_markup=reply_markup)

        elapsed = round(time.monotonic() - t_start, 2)
        logger.info(prefix + 'Готово за ' + str(elapsed) + 'с')
        record_stats(category, platform, elapsed, success)
        return row_id or ''

    except Exception as e:
        elapsed = round(time.monotonic() - t_start, 2)
        logger.error(prefix + 'process_task failed: ' + str(e), exc_info=True)
        record_stats('', platform, elapsed, False)
        raise

    finally:
        # ── 6. Гарантированное удаление файлов после выполнения ───
        for path in task.get('content', {}).get('local_files', []):
            try:
                import os
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
