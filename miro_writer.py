# miro_writer.py
#
# РЕШЕНИЕ ПРОБЛЕМЫ С ПОЗИЦИЕЙ ИЗОБРАЖЕНИЙ:
#
# Multipart/form-data загрузка изображений в Miro API имеет баг:
# параметры position и parent в form-data игнорируются.
# Источник: https://community.miro.com/developer-platform-and-apis-57/
#           image-upload-does-not-use-position-and-parent-link-property-16686
#
# Правильный метод (официальная документация Miro):
# POST /images с Content-Type: application/json
# Изображение кодируется в base64 и передаётся в data.url
# Источник: https://developers.miro.com/docs/create-an-image-from-a-data-url-source
#
# При наличии parent.id координаты position относительны верхнего
# левого угла фрейма — одинаково для images, cards, sticky_notes.

import re
import os
import base64
import requests
import logging
from datetime import datetime
from config import cfg
from detector import ContentType

logger = logging.getLogger(__name__)

MIRO_API = 'https://api.miro.com/v2'

IMG_W  = 400.0
IMG_H  = 300.0
CARD_W = 320.0
CARD_H = 200.0
MARGIN = 24.0
GAP    = 16.0


def safe_str(value) -> str:
    return '' if value is None else str(value)


def get_headers():
    return {
        'Authorization': 'Bearer ' + cfg.MIRO_TOKEN,
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }


# ── Позиционирование (относительно фрейма) ────────────────────────────────────

def get_frame_width(frame_id: str) -> float:
    try:
        url  = MIRO_API + '/boards/' + cfg.MIRO_BOARD_ID + '/frames/' + frame_id
        resp = requests.get(url, headers=get_headers(), timeout=10)
        if resp.ok:
            return float(resp.json().get('geometry', {}).get('width', 1400))
    except Exception as e:
        logger.warning('get_frame_width: ' + str(e))
    return 1400.0


def count_items_in_frame(frame_id: str) -> int:
    try:
        url  = MIRO_API + '/boards/' + cfg.MIRO_BOARD_ID + '/items'
        resp = requests.get(
            url, headers=get_headers(),
            params={'parent_item_id': frame_id, 'limit': 50},
            timeout=10
        )
        if resp.ok:
            return len(resp.json().get('data', []))
    except Exception as e:
        logger.warning('count_items_in_frame: ' + str(e))
    return 0


def next_slot(frame_id: str, item_w: float, item_h: float) -> dict:
    """
    Координаты ЦЕНТРА следующего виджета относительно фрейма.
    Одинаково работает для images (base64), cards, sticky_notes.
    """
    frame_w = get_frame_width(frame_id)
    cols    = max(1, int((frame_w - MARGIN) / (item_w + MARGIN)))
    count   = count_items_in_frame(frame_id)

    col = count % cols
    row = count // cols
    cx  = MARGIN + col * (item_w + MARGIN) + item_w / 2
    cy  = MARGIN + row * (item_h + MARGIN) + item_h / 2

    logger.info(
        str(int(item_w)) + 'x' + str(int(item_h)) +
        ' / фрейм ' + str(int(frame_w)) + 'px / ' + str(cols) + ' кол' +
        ' / #' + str(count + 1) + ' → (' + str(int(cx)) + ', ' + str(int(cy)) + ')'
    )
    return {'x': cx, 'y': cy}


# ── HTML ──────────────────────────────────────────────────────────────────────

def text_to_html(text) -> str:
    text = safe_str(text)
    if not text:
        return ''
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(
        r'(https?://[^\s&]+)',
        lambda m: '<a href="' + m.group(1) + '">' + m.group(1) + '</a>',
        text
    )
    return text.replace('\n', '<br>')


# ── Image через base64+JSON ───────────────────────────────────────────────────

def upload_image_base64(local_path: str, frame_id: str, pos: dict) -> str:
    """
    Загружает изображение через base64 data URL.
    Метод: POST /images с Content-Type: application/json
    
    Это единственный способ при котором position и parent
    гарантированно применяются корректно (multipart form-data
    игнорирует эти параметры — известный баг Miro API).
    
    Координаты pos — относительно верхнего левого угла фрейма.
    """
    try:
        with open(local_path, 'rb') as f:
            image_bytes = f.read()

        ext = local_path.lower().split('.')[-1]
        mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/' + ext
        b64  = base64.b64encode(image_bytes).decode('utf-8')
        data_url = 'data:' + mime + ';base64,' + b64

        payload = {
            'data': {
                'url':   data_url,
                'title': '',
            },
            'position': {
                'x':      pos['x'],
                'y':      pos['y'],
                'origin': 'center',
            },
            'parent': {
                'id': frame_id,
            },
            'geometry': {
                'width': IMG_W,
            },
        }

        url  = MIRO_API + '/boards/' + cfg.MIRO_BOARD_ID + '/images'
        resp = requests.post(url, json=payload, headers=get_headers(), timeout=30)

        if not resp.ok:
            logger.error('Image upload ' + str(resp.status_code) + ': ' + resp.text)
            return ''

        item_id = resp.json().get('id', '')
        logger.info('Image загружен (base64): ' + item_id)
        return item_id

    except Exception as e:
        logger.error('upload_image_base64: ' + str(e))
        return ''


# ── Card ──────────────────────────────────────────────────────────────────────

def post_card(title: str, description_html: str, frame_id: str, pos: dict) -> str:
    payload = {
        'data': {
            'title':       safe_str(title)[:120],
            'description': safe_str(description_html),
        },
        'position': {'x': pos['x'], 'y': pos['y']},
        'parent':   {'id': frame_id},
        'geometry': {'width': CARD_W},
    }
    url  = MIRO_API + '/boards/' + cfg.MIRO_BOARD_ID + '/cards'
    resp = requests.post(url, json=payload, headers=get_headers(), timeout=10)

    if not resp.ok:
        logger.error('Card ' + str(resp.status_code) + ': ' + resp.text)
        resp.raise_for_status()

    return resp.json().get('id', '')


# ── Содержимое Card ───────────────────────────────────────────────────────────

def build_card_content(task: dict, theme: str, meta: dict) -> tuple:
    content = task['content']
    ctype   = content['type']
    date    = datetime.now().strftime('%d.%m.%Y %H:%M')
    subhead = '<i>Тема: ' + theme + ' · ' + date + '</i>'

    if ctype == ContentType.TEXT:
        raw   = safe_str(content.get('raw_text'))
        lines = [l for l in raw.split('\n') if l.strip()]
        title = lines[0][:120] if lines else 'Текст'
        body  = subhead + '<br><br>' + text_to_html(raw)
        return title, body

    elif ctype == ContentType.PHOTO:
        raw   = safe_str(content.get('raw_text'))
        lines = [l for l in raw.split('\n') if l.strip()]
        title = lines[0][:120] if lines else 'Подпись к фото'
        body  = subhead + '<br><br>' + text_to_html(raw)
        return title, body

    elif ctype in (ContentType.VIDEO, ContentType.AUDIO):
        raw   = safe_str(content.get('raw_text'))
        title = raw.split('\n')[0][:120] if raw else ctype.value
        body  = subhead + ('<br><br>' + text_to_html(raw) if raw else '')
        return title, body

    else:
        title = safe_str(meta.get('title')) or 'Ссылка'
        desc  = safe_str(meta.get('description'))
        url   = safe_str(content.get('url'))
        parts = [subhead]
        if desc:
            parts.append('<br><br>' + text_to_html(desc))
        if url:
            parts.append('<br><br><a href="' + url + '">Открыть ссылку →</a>')
        return title[:120], ''.join(parts)


# ── Главная функция ───────────────────────────────────────────────────────────

def miro_url(item_id: str) -> str:
    return 'https://miro.com/app/board/' + cfg.MIRO_BOARD_ID + '/?moveToWidget=' + item_id


async def write_to_miro(task: dict, theme: str, meta: dict,
                        local_photo_path: str = '') -> str:
    content  = task['content']
    ctype    = content['type']

    frame_id = cfg.MIRO_FRAMES.get(theme)
    if not frame_id:
        frame_id = cfg.MIRO_FRAMES.get(cfg.DEFAULT_FRAME, '')
        theme    = cfg.DEFAULT_FRAME

    has_file    = bool(local_photo_path and os.path.exists(local_photo_path))
    has_caption = safe_str(content.get('raw_text')).strip() != ''

    try:
        last_id = ''

        if ctype == ContentType.PHOTO and has_file:

            # Позиция изображения — относительно фрейма
            img_pos = next_slot(frame_id, IMG_W, IMG_H)
            img_id  = upload_image_base64(local_photo_path, frame_id, img_pos)
            if img_id:
                last_id = img_id

            # Если есть подпись — Card правее изображения
            if has_caption and img_id:
                card_pos = {
                    'x': img_pos['x'] + IMG_W / 2 + GAP + CARD_W / 2,
                    'y': img_pos['y'],
                }
                card_title, card_html = build_card_content(task, theme, meta)
                card_id = post_card(card_title, card_html, frame_id, card_pos)
                if card_id:
                    last_id = card_id

        else:
            pos                   = next_slot(frame_id, CARD_W, CARD_H)
            card_title, card_html = build_card_content(task, theme, meta)
            last_id               = post_card(card_title, card_html, frame_id, pos)

        if not last_id:
            return ''

        result = miro_url(last_id)
        logger.info('Готово: ' + result)
        return result

    except requests.exceptions.HTTPError:
        return ''
    except Exception as e:
        logger.error('write_to_miro: ' + str(e))
        return ''
