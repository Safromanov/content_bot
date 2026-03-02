# transcription.py

import os
import re
import html
import logging
import hashlib
import requests as req
from config import cfg
from groq_client import get_groq

logger = logging.getLogger(__name__)

WHISPER_SIZE_LIMIT = 25 * 1024 * 1024
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


# ── Whisper ───────────────────────────────────────────────────────────────────

def transcribe_file(file_path: str) -> str:
    try:
        if not os.path.exists(file_path):
            return ""
        if os.path.getsize(file_path) > WHISPER_SIZE_LIMIT:
            logger.warning("transcribe_file: > 25 MB")
            return ""
        with open(file_path, "rb") as f:
            resp = get_groq().audio.transcriptions.create(
                model="whisper-large-v3", 
                file=f, 
                response_format="text",
                prompt="Это аудиозапись речи на русском или английском языке."
            )
        text = resp.strip() if isinstance(resp, str) else getattr(resp, "text", "").strip()
        logger.info("Whisper: " + str(len(text)) + " символов")
        return text
    except Exception as e:
        logger.error("transcribe_file: " + str(e))
        return ""


def format_transcript(raw: str) -> str:
    if not raw or len(raw.strip()) < 50:
        return raw
    CHUNK = 4000
    parts = []
    for idx, chunk in enumerate([raw[i:i+CHUNK] for i in range(0, len(raw), CHUNK)]):
        try:
            r = get_groq().chat.completions.create(
                model=cfg.GROQ_MODEL_FAST, max_tokens=1500, temperature=0.1,
                messages=[{"role": "user", "content": (
                    "Отредактируй эту транскрипцию речи. Расставь знаки препинания и разбей на абзацы. "
                    "Оставь текст на оригинальном языке. НЕ сокращай текст. "
                    "Если текст выглядит как случайный набор букв из-за ошибки распознавания шума, просто верни ответ: [Шум]. "
                    "Иначе выведи ТОЛЬКО отредактированный текст.\n\n"
                    + chunk
                )}]
            )
            part = r.choices[0].message.content.strip()
            parts.append(part if len(part) >= len(chunk) * 0.4 else chunk)
        except Exception as e:
            logger.warning("format_transcript chunk " + str(idx) + ": " + str(e))
            parts.append(chunk)
    result = "\n\n".join(parts)
    logger.info("Транскрипт: " + str(len(result)) + " символов")
    return result


# ── YouTube субтитры ──────────────────────────────────────────────────────────

def get_youtube_transcript(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", url)
        if not m:
            return ""
        video_id = m.group(1)
        api = YouTubeTranscriptApi()
        try:
            tl = api.list(video_id)
        except AttributeError:
            tl = YouTubeTranscriptApi.list_transcripts(video_id)
        obj = None
        for lang in ["ru", "en"]:
            try:
                obj = tl.find_transcript([lang]); break
            except Exception:
                continue
        if not obj:
            try:
                obj = next(iter(tl))
            except StopIteration:
                return ""
        entries = obj.fetch()
        def gt(e):
            return (e.get("text","") if isinstance(e,dict) else getattr(e,"text","")).strip()
        paragraphs, group = [], []
        for i, e in enumerate(entries):
            group.append(gt(e))
            if (i+1)%5==0:
                paragraphs.append(" ".join(group)); group=[]
        if group: paragraphs.append(" ".join(group))
        full = "\n\n".join(paragraphs)
        logger.info("YouTube субтитры: " + str(len(full)) + " символов")
        return full
    except Exception as e:
        err = str(e)
        if "disabled" in err.lower() or "no transcript" in err.lower():
            logger.info("YouTube: субтитры отключены")
        else:
            logger.warning("get_youtube_transcript: " + err[:200])
        return ""


# ── yt-dlp утилиты ────────────────────────────────────────────────────────────

def _base_ydl_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True}
    cookies = cfg.INSTAGRAM_COOKIES_FILE
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    return opts


def _extract_author_ydl(info: dict) -> str:
    return (info.get("uploader") or info.get("channel") or
            info.get("creator") or info.get("artist") or "")


def download_audio_ydl(url: str, out_path: str) -> str:
    """Скачивает аудиодорожку → mp3. Возвращает путь или пустую строку."""
    try:
        import yt_dlp
        opts = {
            **_base_ydl_opts(),
            "format": "bestaudio/best",
            "outtmpl": out_path,
            "noplaylist": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        mp3 = out_path + ".mp3"
        if os.path.exists(mp3):
            return mp3
        for fname in os.listdir(os.path.dirname(out_path) or "."):
            if fname.startswith(os.path.basename(out_path)):
                return os.path.join(os.path.dirname(out_path) or ".", fname)
        return ""
    except Exception as e:
        logger.warning("download_audio_ydl: " + str(e)[:200])
        return ""


# ── Instagram: вспомогательные ───────────────────────────────────────────────

def _extract_instagram_shortcode(url: str) -> str:
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else ""


def _clean_instagram_url(url: str) -> str:
    """Убирает ?img_index=4&igsh=... перед передачей в API."""
    return url.split("?")[0].rstrip("/")


def _download_image(img_url: str, fpath: str) -> bool:
    """Скачивает изображение с CDN Instagram."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.instagram.com/",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        resp = req.get(img_url, headers=headers, timeout=30)
        resp.raise_for_status()
        with open(fpath, "wb") as f:
            f.write(resp.content)
        logger.info("Скачано: " + os.path.basename(fpath) +
                    " (" + str(len(resp.content) // 1024) + " КБ)")
        return True
    except Exception as e:
        logger.warning("_download_image: " + str(e)[:100])
        return False


# ── Instagram: три независимых источника метаданных ──────────────────────────

def _ig_source_instaloader(shortcode: str) -> dict:
    """
    Источник A: instaloader.
    Полный caption, автор, CDN-ссылки на все изображения карусели.
    При ошибке авторизации возвращает ok=False без исключения.
    """
    out = {"post_type": "", "author": "", "description": "",
           "image_urls": [], "media_items": [], "video_url": "", "ok": False}
    try:
        import instaloader
        L = instaloader.Instaloader(
            download_pictures=False, download_videos=False,
            download_video_thumbnails=False, save_metadata=False, quiet=True,
        )
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        out["author"]      = (post.owner_username or "").strip()
        out["description"] = (post.caption or "").strip()

        nodes = []
        try:
            nodes = list(post.get_sidecar_nodes())
        except Exception:
            pass

        if nodes:
            out["post_type"] = "carousel"
            for node in nodes:
                url_node = (node.video_url if node.is_video else node.display_url) or ""
                out["image_urls"].append(url_node)
                out["media_items"].append({"url": url_node, "is_video": node.is_video})
        elif post.is_video:
            out["post_type"] = "video"
            out["video_url"] = post.video_url or ""
            out["media_items"].append({"url": out["video_url"], "is_video": True})
        else:
            out["post_type"] = "photo"
            out["image_urls"] = [post.url or ""]
            out["media_items"].append({"url": post.url or "", "is_video": False})

        out["ok"] = True
        logger.info("instaloader OK: type=" + out["post_type"] +
                    " items=" + str(len(out["media_items"])) +
                    " desc_len=" + str(len(out["description"])) +
                    " author=" + out["author"])
    except ImportError:
        logger.warning("instaloader не установлен: pip install instaloader")
    except Exception as e:
        logger.warning("instaloader: " + str(e)[:150])
    return out


def _ig_source_oembed(clean_url: str) -> dict:
    """
    Источник B: Instagram oEmbed API (публичный, без авторизации).
    Поле title содержит полный caption поста.
    """
    out = {"author": "", "description": "", "thumbnail_url": "", "ok": False}
    try:
        resp = req.get(
            "https://api.instagram.com/oembed",
            params={"url": clean_url, "format": "json", "omitscript": True},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        out["description"]   = (data.get("title") or "").strip()
        out["author"]        = (data.get("author_name") or "").strip()
        out["thumbnail_url"] = (data.get("thumbnail_url") or "").strip()
        out["ok"]            = True
        logger.info("oEmbed OK: author=" + out["author"] +
                    " desc_len=" + str(len(out["description"])))
    except Exception as e:
        logger.warning("oEmbed: " + str(e)[:100])
    return out


def _ig_source_scrape(clean_url: str) -> dict:
    """
    Источник C: HTTP-скрейпинг страницы Instagram.
    User-Agent facebookexternalhit — Instagram отдаёт og-теги для краулеров.
    og:description = первые ~125 символов caption.
    og:image = превью первого изображения.
    Работает полностью без авторизации.
    """
    out = {"author": "", "description": "", "image_url": "", "ok": False}
    try:
        headers = {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = req.get(clean_url, headers=headers, timeout=15)
        resp.raise_for_status()
        page = resp.text

        # ВАЖНО: используем (?P<q>["\'']) для захвата типа кавычки
        # og:description содержит caption
        desc_m = re.search(
            r'<meta[^>]+property=(?P<q>["\'])og:description(?P=q)[^>]+content=(?P<q2>["\'])(?P<val>.*?)(?P=q2)',
            page, re.IGNORECASE | re.DOTALL
        )
        if desc_m:
            out["description"] = html.unescape(desc_m.group("val")).strip()

        img_m = re.search(
            r'<meta[^>]+property=(?P<q>["\'])og:image(?P=q)[^>]+content=(?P<q2>["\'])(?P<val>[^"\']+)(?P=q2)',
            page, re.IGNORECASE
        )
        if img_m:
            out["image_url"] = img_m.group("val").strip()

        out["ok"] = bool(out["description"] or out["image_url"])
        if out["ok"]:
            logger.info("scrape OK: desc_len=" + str(len(out["description"])) +
                        " img=" + str(bool(out["image_url"])))
        else:
            logger.warning("scrape: страница получена но og-теги не найдены")
    except Exception as e:
        logger.warning("scrape: " + str(e)[:100])
    return out


def _merge_ig_metadata(src_a: dict, src_b: dict, src_c: dict) -> dict:
    """
    Приоритет: instaloader (A) > oEmbed (B) > scrape (C).
    Каждое поле берётся из первого непустого источника.
    """
    return {
        "post_type":     src_a.get("post_type") or "unknown",
        "author":        src_a.get("author") or src_b.get("author") or src_c.get("author") or "",
        "description":   src_a.get("description") or src_b.get("description") or src_c.get("description") or "",
        "image_urls":    src_a.get("image_urls") or [],
        "media_items":   src_a.get("media_items") or [],
        "video_url":     src_a.get("video_url") or "",
        "thumbnail_url": src_b.get("thumbnail_url") or src_c.get("image_url") or "",
    }


# ── Instagram: главная точка входа ───────────────────────────────────────────

def process_instagram_url(url: str, tmp_dir: str) -> dict:
    """
    Двухфазная обработка Instagram URL.

    Фаза 1 — метаданные (три независимых источника, не fallback-цепочка):
      A. instaloader  — полный caption, автор, CDN URL всех изображений карусели
      B. oEmbed API   — caption (обрезан до ~125 символов), автор, thumbnail
      C. HTTP scrape  — og:description (caption ~125 символов), og:image

    Фаза 2 — медиафайлы:
      photo/carousel  → скачиваем изображения по CDN URL (или thumbnail)
      video           → yt-dlp audio → Whisper

    Гарантия: description НЕ будет пустым если хоть один источник отвечает.
    """
    result = {
        "post_type":   "unknown",
        "transcript":  "",
        "author":      "",
        "title":       "",
        "description": "",
        "media_files": [],
    }

    clean_url = _clean_instagram_url(url)
    shortcode = _extract_instagram_shortcode(url)
    url_hash  = hashlib.md5(url.encode()).hexdigest()[:10]

    if not shortcode:
        logger.error("process_instagram_url: shortcode not found: " + url[:80])
        return result

    # ── Фаза 1: три источника ──────────────────────────────
    src_a = _ig_source_instaloader(shortcode)
    src_b = _ig_source_oembed(clean_url)
    src_c = _ig_source_scrape(clean_url)
    meta  = _merge_ig_metadata(src_a, src_b, src_c)

    result["post_type"]   = meta["post_type"]
    result["author"]      = meta["author"]
    result["description"] = meta["description"]
    result["title"]       = " ".join(meta["description"].split()[:20]) if meta["description"] else ""

    sources = [k for k, v in {"instaloader": src_a["ok"], "oembed": src_b["ok"], "scrape": src_c["ok"]}.items() if v]
    logger.info("Instagram метаданные: type=" + result["post_type"] +
                " author=" + result["author"] +
                " desc_len=" + str(len(result["description"])) +
                " sources=" + str(sources))

    # ── Фаза 2: медиафайлы ─────────────────────────────────
    if meta["post_type"] in ("photo", "carousel") and meta["media_items"]:
        video_transcribed = False
        for i, item in enumerate(meta["media_items"][:cfg.CAROUSEL_MAX_IMAGES]):
            url_i = item.get("url")
            is_video = item.get("is_video", False)
            if not url_i:
                continue
            
            ext = "mp4" if is_video else "jpg"
            fpath = os.path.join(tmp_dir, "ig_" + url_hash + "_" + str(i).zfill(3) + "." + ext)
            if _download_image(url_i, fpath):
                result["media_files"].append(fpath)
                
                # Транскрибируем первое видео из карусели
                if is_video and not video_transcribed:
                    video_transcribed = True
                    logger.info("Медиа: carousel video [" + str(i) + "] → Whisper")
                    out_base = os.path.join(tmp_dir, "ig_audio_car_" + url_hash)
                    audio_path = download_audio_ydl(url_i, out_base)
                    if audio_path:
                        try:
                            raw = transcribe_file(audio_path)
                            if raw:
                                result["transcript"] = format_transcript(raw)
                                logger.info("Транскрипт (карусель): " + str(len(result["transcript"])) + " символов")
                        finally:
                            if os.path.exists(audio_path):
                                os.remove(audio_path)
                                
        logger.info("Медиа: " + str(len(result["media_files"])) +
                    "/" + str(len(meta["media_items"])) + " элементов скачано")

    elif meta["post_type"] == "video":
        logger.info("Медиа: video → yt-dlp → Whisper")
        out_base   = os.path.join(tmp_dir, "ig_audio_" + url_hash)
        audio_path = download_audio_ydl(url, out_base)
        if audio_path:
            try:
                raw = transcribe_file(audio_path)
                if raw:
                    result["transcript"] = format_transcript(raw)
                    logger.info("Транскрипт: " + str(len(result["transcript"])) + " символов")
            finally:
                if os.path.exists(audio_path):
                    os.remove(audio_path)

    # Нет изображений из instaloader → thumbnail из oEmbed/scrape
    if not result["media_files"] and meta["thumbnail_url"]:
        fpath = os.path.join(tmp_dir, "ig_" + url_hash + "_thumb.jpg")
        if _download_image(meta["thumbnail_url"], fpath):
            result["media_files"].append(fpath)
            if result["post_type"] == "unknown":
                result["post_type"] = "photo"
            logger.info("Медиа: thumbnail из oEmbed/scrape")

    return result


# ── TikTok / VK ──────────────────────────────────────────────────────────────

def process_social_url_ydl(url: str, tmp_dir: str) -> dict:
    """TikTok / VK через yt-dlp. Возвращает тот же формат что process_instagram_url."""
    result = {
        "post_type": "unknown", "transcript": "",
        "author": "", "title": "", "description": "", "media_files": [],
    }
    try:
        import yt_dlp
        opts = {**_base_ydl_opts(), "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        ext      = (info.get("ext") or "").lower()
        is_video = ext not in IMAGE_EXTS and bool(info.get("acodec") and info.get("acodec") != "none")

        result["title"]       = info.get("title", "") or ""
        result["description"] = info.get("description", "") or ""
        result["author"]      = _extract_author_ydl(info)
        result["post_type"]   = "video" if is_video else "photo"
        logger.info("yt-dlp: type=" + result["post_type"] + " author=" + result["author"][:30])

        if is_video:
            out_base   = os.path.join(tmp_dir, "ydl_" + hashlib.md5(url.encode()).hexdigest()[:12])
            audio_path = download_audio_ydl(url, out_base)
            if audio_path:
                try:
                    raw = transcribe_file(audio_path)
                    if raw:
                        result["transcript"] = format_transcript(raw)
                finally:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
    except Exception as e:
        logger.warning("process_social_url_ydl: " + str(e)[:200])
    return result


# ── VK Wall-пост ──────────────────────────────────────────────────────────────

# Приоритет размеров VK фото: от наибольшего к наименьшему
_VK_PHOTO_SIZE_PRIORITY = ['w', 'z', 'y', 'x', 'r', 'q', 'p', 'o', 'm', 's']


def _vk_best_photo_url(sizes: list) -> str:
    """Возвращает URL фото с наилучшим доступным разрешением."""
    by_type = {s.get('type'): s.get('url', '') for s in sizes if s.get('url')}
    for t in _VK_PHOTO_SIZE_PRIORITY:
        if t in by_type:
            return by_type[t]
    # fallback: последний размер в списке
    return sizes[-1].get('url', '') if sizes else ''


def process_vk_wall_post(url: str, tmp_dir: str) -> dict:
    """
    Получает VK wall-пост через VK API.
    - Извлекает текст поста (description)
    - Скачивает все фотографии из attachments
    Возвращает dict в том же формате что process_instagram_url / process_social_url_ydl.
    """
    result = {
        'post_type':   'photo',
        'transcript':  '',
        'author':      '',
        'title':       '',
        'description': '',
        'media_files': [],
    }

    # ── Парсим owner_id и post_id из URL ─────────────────────────────────────
    # vk.com/wall-63951818_297478  →  -63951818_297478
    m = re.search(r'vk\.(?:com|ru)/wall(-?\d+_\d+)', url)
    if not m:
        logger.error('process_vk_wall_post: не удалось разобрать URL: ' + url[:80])
        return result
    post_id = m.group(1)   # e.g. "-63951818_297478"

    token = cfg.VK_SERVICE_TOKEN
    if not token:
        logger.error('process_vk_wall_post: VK_SERVICE_TOKEN не задан в .env')
        return result

    # ── Запрос к VK API ───────────────────────────────────────────────────────
    try:
        api_url = 'https://api.vk.com/method/wall.getById'
        params = {
            'posts':        post_id,
            'extended':     1,       # включает author info
            'fields':       'name',
            'v':            cfg.VK_API_VERSION,
            'access_token': token,
        }
        resp = req.get(api_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'error' in data:
            err = data['error']
            logger.error('VK API error: ' + str(err.get('error_msg', data['error'])))
            return result

        items = data.get('response', {}).get('items') or data.get('response') or []
        if not items:
            logger.warning('process_vk_wall_post: пустой ответ VK API для ' + post_id)
            return result

        post = items[0]
    except Exception as e:
        logger.error('process_vk_wall_post: ошибка VK API: ' + str(e)[:200])
        return result

    # ── Текст поста ───────────────────────────────────────────────────────────
    text = (post.get('text') or '').strip()
    result['description'] = text
    result['title']       = ' '.join(text.split()[:20]) if text else ''
    logger.info('VK wall-пост: post_id=' + post_id + ' text_len=' + str(len(text)))

    # ── Автор ─────────────────────────────────────────────────────────────────
    # extended=1 возвращает groups[] или profiles[]
    owner_id = post.get('owner_id', 0)
    groups   = data.get('response', {}).get('groups') or []
    profiles = data.get('response', {}).get('profiles') or []
    if owner_id < 0:   # группа
        for g in groups:
            if g.get('id') == abs(owner_id):
                result['author'] = g.get('name', '')
                break
    else:
        for p in profiles:
            if p.get('id') == owner_id:
                result['author'] = (p.get('first_name', '') + ' ' + p.get('last_name', '')).strip()
                break

    # ── Скачиваем фотографии ──────────────────────────────────────────────────
    url_hash    = hashlib.md5(url.encode()).hexdigest()[:10]
    attachments = post.get('attachments') or []
    photo_count = 0

    for i, att in enumerate(attachments):
        if att.get('type') != 'photo':
            continue
        photo = att.get('photo') or {}
        sizes = photo.get('sizes') or []
        if not sizes:
            continue
        photo_url = _vk_best_photo_url(sizes)
        if not photo_url:
            continue

        fpath = os.path.join(tmp_dir, 'vk_' + url_hash + '_' + str(i).zfill(3) + '.jpg')
        if _download_image(photo_url, fpath):
            result['media_files'].append(fpath)
            photo_count += 1

    result['post_type'] = 'photo' if photo_count > 0 else ('text' if text else 'unknown')
    logger.info('VK wall-пост: скачано ' + str(photo_count) + ' фото, автор=' + result['author'][:30])
    return result




