# extractor.py
# Обёртка для обратной совместимости.
# Весь реальный код перенесён в:
#   transcription.py — Whisper, yt-dlp, YouTube субтитры
#   metadata.py      — oEmbed, og:title
#   text_utils.py    — analyze_photo, generate_theme_title, generate_summary

from transcription import (
    transcribe_file,
    format_transcript,
    get_youtube_transcript,
    get_media_info_ydl,
    download_audio_ydl,
    transcribe_url,
)

from metadata import (
    extract_youtube_metadata,
    extract_page_metadata,
    extract_metadata,
)

from text_utils import (
    analyze_photo,
    generate_theme_title,
    generate_summary,
    generate_video_description,
)


def _meaningful_text(raw: str, url: str) -> str:
    if not raw:
        return ''
    cleaned = raw.replace(url, '').strip() if url else raw.strip()
    return cleaned if len(cleaned) >= 4 else ''


__all__ = [
    'transcribe_file', 'format_transcript', 'get_youtube_transcript',
    'get_media_info_ydl', 'download_audio_ydl', 'transcribe_url',
    'extract_youtube_metadata', 'extract_page_metadata', 'extract_metadata',
    'analyze_photo', 'generate_theme_title', 'generate_summary',
    'generate_video_description', '_meaningful_text',
]
