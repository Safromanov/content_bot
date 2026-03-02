# detector.py

import re
from enum import Enum


class ContentType(Enum):
    YOUTUBE   = 'youtube'
    INSTAGRAM = 'instagram'
    TIKTOK    = 'tiktok'
    TELEGRAM  = 'telegram'
    VK        = 'vk'
    VK_WALL   = 'vk_wall'
    LINKEDIN  = 'linkedin'
    OTHER_URL = 'other_url'
    VIDEO     = 'video'
    AUDIO     = 'audio'
    PHOTO     = 'photo'
    TEXT      = 'text'
    UNKNOWN   = 'unknown'


PATTERNS = {
    ContentType.YOUTUBE:   r'(youtube\.com/watch|youtu\.be/|youtube\.com/shorts)',
    ContentType.INSTAGRAM: r'instagram\.com/(p|reel|tv)/',
    ContentType.TIKTOK:    r'(tiktok\.com|vm\.tiktok\.com)',
    ContentType.TELEGRAM:  r't\.me/',
    ContentType.VK_WALL:   r'vk\.(com|ru)/wall',
    ContentType.VK:        r'(vk\.(com|ru)|vkvideo\.ru)',
    ContentType.LINKEDIN:  r'linkedin\.com/(posts|feed|pulse)/',
    ContentType.OTHER_URL: r'https?://\S+',
}

# Маппинг тип → название платформы для поля Platform в NocoDB
PLATFORM_LABELS = {
    ContentType.YOUTUBE:   'YouTube',
    ContentType.INSTAGRAM: 'Instagram',
    ContentType.TIKTOK:    'TikTok',
    ContentType.TELEGRAM:  'Telegram',
    ContentType.VK:        'VK',
    ContentType.VK_WALL:   'VK',
    ContentType.LINKEDIN:  'LinkedIn',
    ContentType.OTHER_URL: 'Link',
    ContentType.VIDEO:     'Video',
    ContentType.AUDIO:     'Audio',
    ContentType.PHOTO:     'Photo',
    ContentType.TEXT:      'Text',
    ContentType.UNKNOWN:   'Other',
}


def _reconstruct_markdown(message) -> str:
    """Извлекает текст сообщения и превращает гиперссылки (entities) в [текст](url)."""
    text = message.text or message.caption or ""
    if not text:
        return ""
    
    entities = message.entities or message.caption_entities
    if not entities:
        return text

    # Сортируем сущности с конца, чтобы вставка Markdown не ломала офсеты
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
    
    for e in sorted_entities:
        start = e.offset
        end = e.offset + e.length
        
        # Обрабатываем только скрытые ссылки (text_link)
        if e.type == 'text_link':
            link_text = text[start:end]
            url = e.url
            if url:
                text = text[:start] + f"[{link_text}]({url})" + text[end:]
        # Можно добавить поддержку bold/italic аналогично, но пока фокус на ссылках
                
    return text


def _extract_forward_author(message) -> str:
    """Извлекает имя автора/канала из пересланного сообщения (PTB v20+)."""
    if getattr(message, 'forward_origin', None):
        origin = message.forward_origin
        if origin.type == 'channel' and origin.chat:
            return origin.chat.title or ""
        elif origin.type == 'chat' and origin.sender_chat:
            return origin.sender_chat.title or ""
        elif origin.type == 'user' and origin.sender_user:
            return origin.sender_user.full_name or ""
        elif origin.type == 'hidden_user':
            return origin.sender_user_name or ""
    return ""


def detect(message) -> dict:
    result = {
        'type':               ContentType.UNKNOWN,
        'url':                None,
        'file_id':            None,
        'raw_text':           None,
        'photo_ids':          [],        # список file_id фото (для поддержки альбомов)
        'media_group_id':     None,      # id альбома если есть
        'vision_description': None,
        'forward_author':     _extract_forward_author(message),
    }

    # ── Сначала вытаскиваем текст с Markdown-ссылками ──────────────
    raw_text = _reconstruct_markdown(message)
    result['raw_text'] = raw_text

    # ── Фото ──────────────────────────────────────────────
    if message.photo:
        result['type']           = ContentType.PHOTO
        result['photo_ids']      = [message.photo[-1].file_id]  # всегда список
        result['media_group_id'] = message.media_group_id
        return result

    # ── Видео ─────────────────────────────────────────────
    if message.video:
        result['type']           = ContentType.VIDEO
        result['file_id']        = message.video.file_id
        result['media_group_id'] = message.media_group_id
        return result

    # ── Аудио / голосовое ─────────────────────────────────
    if message.audio or message.voice:
        obj = message.audio or message.voice
        result['type']    = ContentType.AUDIO
        result['file_id'] = obj.file_id
        return result

    # ── Доп. медиа (Документы) ─────────────────────────────
    if message.document:
        mime = message.document.mime_type or ''
        result['file_id'] = message.document.file_id
        
        if mime.startswith('video/'):
            result['type'] = ContentType.VIDEO
        elif mime.startswith('audio/'):
            result['type'] = ContentType.AUDIO
        elif mime.startswith('image/'):
            result['type'] = ContentType.PHOTO
        else:
            result['type'] = ContentType.UNKNOWN
        return result

    # ── Ссылки на платформы ───────────────────────────────
    # Используем оригинальный текст для детекции типа, чтобы Markdown не мешал
    orig_text = message.text or message.caption or ""
    if not orig_text:
        return result

    for content_type, pattern in PATTERNS.items():
        if re.search(pattern, orig_text, re.IGNORECASE):
            # Ищем кусок текста без пробелов, который совпадает с паттерном
            tokens = orig_text.split()
            for token in tokens:
                if re.search(pattern, token, re.IGNORECASE):
                    result['url'] = token
                    break
            result['type'] = content_type
            return result

    result['type'] = ContentType.TEXT
    return result