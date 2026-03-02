# media_handlers.py
# Стратегия-паттерн: каждая платформа — отдельный класс.
# Все реализуют метод enrich(content, task_id) → dict с обогащёнными данными.
# processor.py выбирает нужный обработчик и вызывает enrich().

import logging
import os
import requests
from abc import ABC, abstractmethod
from detector import ContentType
from transcription import (
    transcribe_file, format_transcript,
    get_youtube_transcript,
    process_instagram_url, process_social_url_ydl,
    process_vk_wall_post,
    download_audio_ydl,
)
from metadata import extract_youtube_metadata, extract_page_metadata
from text_utils import (
    analyze_photo, generate_summary, generate_video_description,
)
from config import cfg

logger = logging.getLogger(__name__)


# ── Интерфейс ────────────────────────────────────────────────────────────────

class BaseMediaHandler(ABC):
    """Базовый класс. enrich() возвращает обогащённый content dict."""

    @abstractmethod
    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        """
        Обогащает content данными платформы.
        Изменяет content in-place И возвращает его.
        Гарантирует наличие ключей: transcript, summary, vision_description,
        platform_description, is_video, local_files.
        meta возвращается отдельно через get_meta().
        """

    def _init_content(self, content: dict) -> None:
        """Инициализирует обязательные поля."""
        content.setdefault('transcript',           '')
        content.setdefault('summary',              '')
        content.setdefault('vision_description',   '')
        content.setdefault('platform_description', '')
        content.setdefault('is_video',             True)
        content.setdefault('local_files',          [])

    def _log(self, task_id: str, msg: str) -> None:
        prefix = '[' + task_id + '] ' if task_id else ''
        logger.info(prefix + msg)

    def _warn(self, task_id: str, msg: str) -> None:
        prefix = '[' + task_id + '] ' if task_id else ''
        logger.warning(prefix + msg)


async def _download_telegram_file(file_id: str, bot, suffix: str, task_id: str) -> str:
    """Общая утилита скачивания файла из Telegram."""
    from config import cfg
    tmp_path = os.path.join(cfg.TEMP_DIR, file_id + '.' + suffix)
    try:
        tg_file  = await bot.get_file(file_id)
        full_url = tg_file.file_path
        masked   = full_url.replace(cfg.TELEGRAM_TOKEN, '[TOKEN]') if cfg.TELEGRAM_TOKEN else full_url
        prefix   = '[' + task_id + '] ' if task_id else ''
        logger.info(prefix + 'Скачиваю: ' + masked)
        resp = requests.get(full_url, timeout=60)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)
        logger.info(prefix + 'Скачан: ' + os.path.basename(tmp_path) +
                    ' (' + str(len(resp.content) // 1024) + ' КБ)')
        return tmp_path
    except Exception as e:
        logger.error('[' + task_id + '] download: ' + str(e))
        return ''


# ── Обработчики ───────────────────────────────────────────────────────────────

class YouTubeHandler(BaseMediaHandler):
    """
    YouTube: параллельно получает oEmbed + субтитры через asyncio.gather.
    Если субтитры недоступны — описание по заголовку.
    """

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio
        self._init_content(content)
        url = content.get('url', '')

        self._log(task_id, 'YouTube: получаем метаданные и субтитры параллельно')

        # Параллельно: oEmbed + субтитры — оба I/O bound
        loop = asyncio.get_event_loop()
        meta_future       = loop.run_in_executor(None, extract_youtube_metadata, url)
        transcript_future = loop.run_in_executor(None, get_youtube_transcript, url)

        meta_result, raw_transcript = await asyncio.gather(
            meta_future, transcript_future
        )

        content['_meta'] = meta_result

        if raw_transcript:
            content['transcript'] = raw_transcript
            if len(raw_transcript) > 300:
                content['summary'] = await loop.run_in_executor(
                    None, generate_summary, raw_transcript
                )
            self._log(task_id, 'YouTube субтитры: ' + str(len(raw_transcript)) + ' символов')
        else:
            self._log(task_id, 'YouTube: субтитры недоступны')
 
        return content


class SocialHandler(BaseMediaHandler):
    """
    Instagram / TikTok / VK.

    Instagram — двухфазная обработка через instaloader:
      Фаза 1: определяем post_type, caption, author, image_urls
      Фаза 2: скачиваем медиа (фото/аудио), vision-анализ изображений,
              транскрипция видео

    Vision запускается для фото и каруселей — модель смотрит
    на первое изображение перед финальной классификацией.
    Результат попадает в content["vision_description"] и используется
    классификатором вместе с описанием и хештегами.

    Все поля NocoDB заполняются для любого типа контента.
    """

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio
        from transcription import process_instagram_url, process_social_url_ydl
        from text_utils import generate_summary, generate_video_description, analyze_photo

        self._init_content(content)
        url     = content.get("url", "")
        raw     = (content.get("raw_text") or "").strip()
        url_str = url or ""
        caption = raw.replace(url_str, "").strip() if url_str else raw
        if len(caption) < 4:
            caption = ""

        ctype = content["type"]
        self._log(task_id, ctype.value + ": определяем тип поста...")

        loop = asyncio.get_event_loop()

        from detector import ContentType
        is_instagram = (ctype == ContentType.INSTAGRAM)

        if is_instagram:
            result = await loop.run_in_executor(None, process_instagram_url, url, cfg.TEMP_DIR)
        else:
            result = await loop.run_in_executor(None, process_social_url_ydl, url, cfg.TEMP_DIR)

        post_type = result.get("post_type", "unknown")

        self._log(task_id,
            "post_type=" + post_type +
            " author=" + result.get("author", "") +
            " desc_len=" + str(len(result.get("description", ""))) +
            " media_files=" + str(len(result.get("media_files", []))) +
            " transcript_len=" + str(len(result.get("transcript", "")))
        )

        content["is_video"]             = (post_type == "video")
        content["transcript"]           = result.get("transcript", "")
        content["platform_description"] = result.get("description", "") or caption

        media_files = result.get("media_files", [])
        if media_files:
            content["local_files"].extend(media_files)

        meta = {
            "title":       result.get("title", "") or caption,
            "author":      result.get("author", ""),
            "description": result.get("description", "") or caption,
            "url":         url,
            "post_type":   post_type,
        }

        # ── Vision: нейросеть смотрит на изображения (карусель) ─────────────
        # Запускаем для фото/каруселей ВСЕГДА — не только когда нет caption.
        # Vision + description + hashtags = наилучшая классификация.
        # Если карусель смешанная (фото+видео), мы комбинируем Vision с первых 3 фото
        if content["local_files"]:
            vision_parts = []
            vision_count = 0
            for file_path in content["local_files"]:
                ext = file_path.lower().split('.')[-1]
                if ext in ("jpg", "jpeg", "png", "webp") and vision_count < 5:
                    self._log(task_id, "Vision: анализируем " + os.path.basename(file_path))
                    v_res = await loop.run_in_executor(None, analyze_photo, file_path)
                    if v_res:
                        # Используем пулю для каждого фото, чтобы избежать Markdown-заголовков
                        vision_parts.append("• " + v_res.strip())
                        vision_count += 1
            if vision_parts:
                full_vision = "\n\n".join(vision_parts)
                content["vision_description"] = full_vision
                self._log(task_id, f"Vision: {vision_count} фото проанализировано")

        # ── Доп. обработка по типу ────────────────────────────────
        if content["transcript"]:
            if len(content["transcript"]) > 300:
                content["summary"] = await loop.run_in_executor(
                    None, generate_summary, content["transcript"]
                )
            self._log(task_id, "Транскрипт: " + str(len(content["transcript"])) + " символов")

        elif post_type == "video" and not result.get("description") and result.get("title"):
            desc = await loop.run_in_executor(
                None, generate_video_description, result["title"]
            )
            meta["description"] = desc or caption
            content["platform_description"] = meta["description"]

        if not meta["description"] and caption:
            meta["description"] = caption
            content["platform_description"] = caption

        # Итоговый источник classify_text для лога
        classify_src = (
            "transcript"    if content["transcript"]           else
            "platform_desc" if content["platform_description"] else
            "vision"        if content.get("vision_description") else
            "empty"
        )
        self._log(task_id, "classify_src=" + classify_src)

        content["_meta"] = meta
        return content


class TelegramVideoHandler(BaseMediaHandler):
    """Telegram видео/аудио: скачивает файл → Whisper → форматирует."""

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio
        self._init_content(content)
        ctype   = content['type']
        file_id = content.get('file_id')

        if not file_id:
            return content

        suffix = 'mp4' if ctype == ContentType.VIDEO else 'mp3'
        path   = await _download_telegram_file(file_id, bot, suffix, task_id)
        if path:
            content['local_files'].append(path)
            self._log(task_id, 'Whisper транскрипция...')

            loop           = asyncio.get_event_loop()
            raw_transcript = await loop.run_in_executor(None, transcribe_file, path)

            if raw_transcript:
                formatted = await loop.run_in_executor(None, format_transcript, raw_transcript)
                content['transcript'] = formatted
                if len(raw_transcript) > 300:
                    content['summary'] = await loop.run_in_executor(
                        None, generate_summary, raw_transcript
                    )
            else:
                raw = (content.get('raw_text') or '').strip()
                if raw:
                    desc = await loop.run_in_executor(
                        None, generate_video_description, raw
                    )
                    content.setdefault('_meta', {})['description'] = desc

        content['_meta'] = content.get('_meta', {'title': '', 'author': '', 'description': '', 'url': ''})
        return content


class PhotoHandler(BaseMediaHandler):
    """Telegram фото: скачивает все фото альбома, vision для первого если нет подписи."""

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio
        self._init_content(content)

        for photo_id in content.get('photo_ids', []):
            path = await _download_telegram_file(photo_id, bot, 'jpg', task_id)
            if path:
                content['local_files'].append(path)

        raw_text = (content.get('raw_text') or '').strip()
        if not raw_text and content['local_files']:
            loop   = asyncio.get_event_loop()
            vision = await loop.run_in_executor(None, analyze_photo, content['local_files'][0])
            content['vision_description'] = vision.strip()
            if vision:
                self._log(task_id, 'Vision: ' + vision[:80])

        content['is_video'] = False
        content['_meta']    = {'title': '', 'author': '', 'description': '', 'url': ''}
        return content


class VkWallHandler(BaseMediaHandler):
    """
    VK wall-пост (vk.com/wall-XXXXXX_YYYYYYY).

    Логика:
      1. process_vk_wall_post() — VK API: text + photos
      2. Vision-анализ всех скачанных фото (до 5 штук)
      3. Классификация по text + vision

    VK видео (vkvideo.ru / прочие ссылки vk.com без /wall) —
    по-прежнему обрабатываются SocialHandler через yt-dlp.
    """

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio

        self._init_content(content)
        url     = content.get('url', '')
        raw     = (content.get('raw_text') or '').strip()
        url_str = url or ''
        caption = raw.replace(url_str, '').strip() if url_str else raw
        if len(caption) < 4:
            caption = ''

        self._log(task_id, 'VK wall-пост: запрашиваем через VK API...')
        loop = asyncio.get_event_loop()

        result = await loop.run_in_executor(
            None, process_vk_wall_post, url, cfg.TEMP_DIR
        )

        post_type = result.get('post_type', 'photo')
        content['is_video']             = False
        content['transcript']           = result.get('transcript', '')
        content['platform_description'] = result.get('description', '') or caption

        media_files = result.get('media_files', [])
        if media_files:
            content['local_files'].extend(media_files)

        meta = {
            'title':     result.get('title', '') or caption,
            'author':    result.get('author', ''),
            'description': result.get('description', '') or caption,
            'url':       url,
            'post_type': post_type,
        }

        # ── Vision: анализируем все скачанные фото ───────────────────────────
        if content['local_files']:
            vision_parts = []
            vision_count = 0
            for file_path in content['local_files']:
                ext = file_path.lower().split('.')[-1]
                if ext in ('jpg', 'jpeg', 'png', 'webp') and vision_count < 5:
                    self._log(task_id, 'Vision: анализируем ' + os.path.basename(file_path))
                    v_res = await loop.run_in_executor(None, analyze_photo, file_path)
                    if v_res:
                        vision_parts.append('• ' + v_res.strip())
                        vision_count += 1
            if vision_parts:
                content['vision_description'] = '\n\n'.join(vision_parts)
                self._log(task_id, 'Vision: ' + str(vision_count) + ' фото проанализировано')

        if not meta['description'] and caption:
            meta['description'] = caption
            content['platform_description'] = caption

        classify_src = (
            'platform_desc' if content['platform_description'] else
            'vision'        if content.get('vision_description') else
            'empty'
        )
        self._log(task_id, 'classify_src=' + classify_src)

        content['_meta'] = meta
        return content


class TextHandler(BaseMediaHandler):
    """Текст, OTHER_URL, LinkedIn — метаданные страницы + platform_description для классификатора."""

    async def enrich(self, content: dict, bot, task_id: str) -> dict:
        import asyncio
        self._init_content(content)
        url = content.get('url', '')

        if url:
            loop        = asyncio.get_event_loop()
            meta_result = await loop.run_in_executor(None, extract_page_metadata, url)
            content['_meta'] = meta_result
            # description → platform_description: приоритет 2 в classify_text
            # без этого статьи Habr/Medium уходят в 'other' — классификатор
            # видел только заголовок без контекста
            desc = (meta_result.get('description') or '').strip()
            if desc:
                content['platform_description'] = desc
                self._log(task_id, 'page desc_len=' + str(len(desc)))
        else:
            content['_meta'] = {'title': '', 'author': '', 'description': '', 'url': ''}

        content['is_video'] = False
        return content


# ── Роутер ────────────────────────────────────────────────────────────────────

_HANDLER_MAP: dict[ContentType, BaseMediaHandler] = {
    ContentType.YOUTUBE:   YouTubeHandler(),
    ContentType.INSTAGRAM: SocialHandler(),
    ContentType.TIKTOK:    SocialHandler(),
    ContentType.VK:        SocialHandler(),
    ContentType.VK_WALL:   VkWallHandler(),
    ContentType.VIDEO:     TelegramVideoHandler(),
    ContentType.AUDIO:     TelegramVideoHandler(),
    ContentType.PHOTO:     PhotoHandler(),
    ContentType.TEXT:      TextHandler(),
    ContentType.LINKEDIN:  TextHandler(),
    ContentType.OTHER_URL: TextHandler(),
    ContentType.TELEGRAM:  TextHandler(),
    ContentType.UNKNOWN:   TextHandler(),
}


def get_handler(ctype: ContentType) -> BaseMediaHandler:
    return _HANDLER_MAP.get(ctype, TextHandler())